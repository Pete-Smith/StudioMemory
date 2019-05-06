""" Project history persistence and operational logic. """
import datetime
from abc import abstractmethod

from sqlalchemy import (
    Enum, Unicode, Column, DateTime, ForeignKey, String, Integer, and_, or_
)
# from sqlalchemy.orm import relationship
from sqlalchemy.orm.session import Session
from sqlalchemy.orm.exc import NoResultFound

from studio_memory import DeclarativeBase
from studio_memory.state import (
    User, ColumnState, SwimlaneState, EntryState
)


class KanbanError(Exception):
    """ Blocks operations disallowed by the Kanban rules.  """
    pass


class Action(DeclarativeBase):
    """ Base class for objects that record the project history.  """
    __tablename__ = 'actions'
    id_ = Column(Integer, primary_key=True)
    user_uid = Column(String(36), ForeignKey('users.uid'))
    column_id = Column(Integer, ForeignKey('columns.id_'))
    swimlane_id = Column(Integer, ForeignKey('swimlanes.id_'))
    entry_id = Column(Integer, ForeignKey('entries.id_'))
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    type_ = Column(String(50))
    __mapper_args__ = {
        'polymorphic_identity': 'action',
        'polymorphic_on': type_
    }

    @abstractmethod
    def validate(self, session: Session):
        """
        Used by the interface to check the validity of actions.
        Called at the top of the apply method by convention.
        """
        raise NotImplementedError()

    @abstractmethod
    def apply(self, session: Session):
        """
        Return None if the action was successful upon the state objects in the
        given session. Otherwise raise an exception.
        The client code will be responsible for managing the transaction.
        """
        raise NotImplementedError()

    def record_current_user(self, session: Session):
        """
        Call this at the top of the apply method to save the checked-in user
         to the user_uid field.
        """
        user = User.current(session)
        self.user_uid = user.uid
        return user


class AddColumn(Action):
    __tablename__ = 'add_column'
    __mapper_args__ = {'polymorphic_identity': 'add_column'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    insertion_index = Column(Integer)

    def __init__(self, insertion_index: int):
        self.insertion_index = insertion_index

    def validate(self, session: Session):
        if session.query(ColumnState).filter(
                ColumnState.id_ == self.column_id
        ).count():
            raise IndexError(
                f'Column with id == {self.column_id} already exists.'
            )

    def apply(self, session: Session) -> ColumnState:
        """
        Inserts an untitled Queue column at the given board_index.
        Returns the new column object.
        """
        self.record_current_user(session)
        self.validate(session)
        for c in session.query(ColumnState)\
                .filter(ColumnState.board_index >= self.insertion_index).all():
            c.board_index += 1
        new_column = ColumnState(board_index=self.insertion_index)
        session.add(new_column)
        session.commit()
        self.column_id = new_column.id_
        session.commit()
        return new_column


class RemoveColumn(Action):
    __tablename__ = 'remove_column'
    __mapper_args__ = {'polymorphic_identity': 'remove_column'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)

    def __init__(self, column_id: int):
        self.column_id = column_id

    def validate(self, session: Session):
        # Raises an exception if the column_id isn't found.
        column = session.query(ColumnState)\
                .filter(ColumnState.id_ == self.column_id).one()
        if column.active_card_count() > 0:
            raise KanbanError(
                'A column cannot be removed if there are active cards on it.'
            )

    def apply(self, session: Session) -> ColumnState:
        """
         Sets the status of the given column to 'removed'.
         Preserves the column's board_index.
        """
        self.record_current_user(session)
        self.validate(session)
        column = session.query(ColumnState)\
            .filter(ColumnState.id_ == self.column_id).one()
        column.status = 'removed'
        session.commit()
        return column


class MoveColumn(Action):
    __tablename__ = 'move_column'
    __mapper_args__ = {'polymorphic_identity': 'move_column'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    new_index = Column(Integer)

    def __init__(self, column: ColumnState, new_index: int):
        self.new_index = new_index
        self.column_id = column.id_

    def validate(self, session: Session):
        # Raises an exception if the column_id isn't found.
        column = session.query(ColumnState)\
                .filter(ColumnState.id_ == self.column_id).one()
        if column.active_card_count() > 0:
            raise KanbanError(
                'A column cannot be moved if there are active cards on it.'
            )

    def apply(self, session: Session) -> ColumnState:
        self.record_current_user(session)
        self.validate(session)
        column = session.query(ColumnState)\
            .filter(ColumnState.id_ == self.column_id).one()
        current_index = column.board_index
        # Subtract one from the column indices equal or higher
        # than our current column.
        for c in session.query(ColumnState)\
                .filter(ColumnState.board_index > current_index):
            if c.id_ != column.id_:
                c.board_index -= 1
        # Add one to the column indices equal or higher
        # than the new index so it fits.
        for c in session.query(ColumnState) \
                .filter(ColumnState.board_index >= self.new_index):
            if c.id_ != column.id_:
                c.board_index += 1
        column.board_index = self.new_index
        session.commit()
        return column


class ModifyColumn(Action):
    __tablename__ = 'modify_column'
    __mapper_args__ = {'polymorphic_identity': 'modify_column'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    valid_fields = ('title', 'done_rule', 'column_type', 'wip_limit', 'status',
                    'line_of_commitment')
    field_name = Column(Enum(*valid_fields))
    field_value = Column(Unicode)

    def __init__(self, column: ColumnState, name: str, value: str):
        """
        Convert values to strings before passing them into this constructor.
        """
        self.column_id = column.id_
        self.field_name = name
        self.field_value = value

    def validate(self, session: Session):
        # Raises an exception if the column_id isn't found.
        column = session.query(ColumnState)\
                .filter(ColumnState.id_ == self.column_id).one()
        active_column_titles = [
            c.title for c in
            session.query(ColumnState).filter(and_(
                ColumnState.status == 'active',
                ColumnState.id_ != self.column_id
            ))
        ]
        if self.field_name not in self.valid_fields:
            raise AttributeError(
                f'{self.field_name} is not a valid field name.'
            )
        if (self.field_name == 'column_type'
                and self.field_value not in ColumnState.column_types):
            raise ValueError(
                f'{self.field_value} is not a valid column type.'
            )
        if self.field_name == 'wip_limit':
            try:
                int(self.field_value)
            except ValueError:
                raise ValueError('WIP limit must be an integer.')
            if int(self.field_value) < 0:
                raise ValueError('WIP limits may not be negative.')
        if (self.field_name == 'status'
                and self.field_value not in ('active', 'removed')):
            raise ValueError(
                f'{self.field_value} is not a valid status for a column.'
            )
        if self.field_name == 'status':
            if self.field_value == 'removed':
                raise ValueError('Use the remove column action instead.')
            elif self.field_value == 'active':
                if column.title in active_column_titles:
                    raise ValueError(
                        'Unable to re-activate column because there is another '
                        'active column with its title.'
                    )
        if self.field_name == 'title':
            if self.field_value in active_column_titles:
                raise ValueError(
                    'Unable to change column title to non-unique value : '
                    + str(self.field_value)
                )
            elif self.field_value.strip() == '':
                raise ValueError('Column titles may not be blank.')

    def apply(self, session: Session) -> ColumnState:
        self.validate(session)
        column = session.query(ColumnState)\
            .filter(ColumnState.id_ == self.column_id).one()
        if self.field_name == 'wip_limit':
            column.wip_limit = int(self.field_value)
        elif self.field_name == 'line_of_commitment':
            column.line_of_commitment = bool(self.field_value)
            for c in session.query(ColumnState).filter().all():
                if c is not column:
                    c.line_of_commitment = False
        else:
            # print(f"{self.field_name} --> {self.field_value}")
            setattr(column, self.field_name, self.field_value)
        session.commit()
        return column


class AddSwimlane(Action):
    __tablename__ = 'add_swimlane'
    __mapper_args__ = {'polymorphic_identity': 'add_swimlane'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    title = Column(String)
    wip_limit = Column(Integer)

    def __init__(self, title: str, wip_limit: int = 0):
        self.title = title
        self.wip_limit = wip_limit

    def validate(self, session: Session):
        # Test that the title is unique among active swimlanes.
        titles = [
            s.title for s in session.query(SwimlaneState)
            .filter(SwimlaneState.status == 'active').all()
        ]
        if self.title in titles:
            raise ValueError(
                'Two active swimlanes with the same title is disallowed.'
            )
        # Test that the wip_limit is greater than zero.
        if self.wip_limit < 0:
            raise ValueError('Negative WIP limits are disallowed.')

    def apply(self, session: Session) -> SwimlaneState:
        self.record_current_user(session)
        self.validate(session)
        new_swimlane = SwimlaneState(
            id_=self.swimlane_id, title=self.title, wip_limit=self.wip_limit
        )
        session.add(new_swimlane)
        session.commit()
        self.swimlane_id = new_swimlane.id_
        session.commit()
        return new_swimlane


class RemoveSwimlane(Action):
    __tablename__ = 'remove_swimlane'
    __mapper_args__ = {'polymorphic_identity':'remove_swimlane'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)

    def __init__(self, swimlane: SwimlaneState):
        self.swimlane_id = swimlane.id_

    def validate(self, session: Session):
        swimlane = session.query(SwimlaneState)\
            .filter(SwimlaneState.id_ == self.swimlane_id).one()
        if swimlane.active_card_count() > 0:
            raise KanbanError(
                'A swimlane cannot be removed if there are active cards on it.'
            )

    def apply(self, session: Session):
        self.validate(session)
        self.record_current_user(session)
        swimlane = session.query(SwimlaneState) \
            .filter(SwimlaneState.id_ == self.swimlane_id).one()
        swimlane.status = 'removed'
        session.commit()
        return swimlane


class ModifySwimlane(Action):
    __tablename__ = 'modify_swimlane'
    __mapper_args__ = {'polymorphic_identity': 'modify_swimlane'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    valid_fields = (
        'title', 'wip_limit', 'status', 'target_start', 'target_end'
    )
    field_name = Column(Enum(*valid_fields))
    field_value = Column(Unicode)

    def __init__(self, swimlane: SwimlaneState, name: str, value: str):
        """
        Convert values to strings before passing them into this constructor.
        """
        self.swimlane_id = swimlane.id_
        self.field_name = name
        self.field_value = value

    def validate(self, session: Session):
        try:
            swimlane = session.query(SwimlaneState)\
                .filter(SwimlaneState.id_ == self.swimlane_id).one()
        except NoResultFound:
            raise IndexError(f'Swimlane id not found : {self.swimlane_id}')
        active_swimlane_titles = [
            s.title for s in
            session.query(SwimlaneState).filter(and_(
                SwimlaneState.status == 'active',
                SwimlaneState.id_ != self.swimlane_id
            ))
        ]
        if self.field_name not in self.valid_fields:
            raise AttributeError(
                f'{self.field_name} is not a valid field name.'
            )
        if self.field_name == 'title':
            if self.field_value in active_swimlane_titles:
                raise ValueError('Duplicate swimlane titles are disallowed.')
            elif self.field_value.strip() == '':
                raise ValueError('Empty swimlane titles are disallowed.')
        elif self.field_name == 'wip_limit':
            try:
                int(self.field_value)
            except ValueError:
                raise ValueError('WIP limits must be integers.')
            if int(self.field_value) < 0:
                raise ValueError('WIP limits can not be negative.')
        elif self.field_name == 'status':
            if self.field_name == 'removed':
                raise ValueError('Use the remove swimlane action instead.')
            elif self.field_value == 'active':
                if self.field_value in active_swimlane_titles:
                    raise ValueError(
                        'Swimlane can not be reactivated because there is '
                        'another active swimlane with the same title.'
                    )
            else:
                raise ValueError(f'Unknown status: {self.field_value}')
        elif self.field_name in ('target_start', 'target_end'):
            try:
                if self.field_value.strip() != '':
                    datetime.datetime.fromisoformat(self.field_value)
            except ValueError:
                raise ValueError(
                    'Target datetime value must be an ISO formatted string '
                    'or a blank string.'
                )

    def apply(self, session: Session):
        self.validate(session)
        self.record_current_user(session)
        swimlane = session.query(SwimlaneState) \
            .filter(SwimlaneState.id_ == self.swimlane_id).one()
        if self.field_name == 'wip_limit':
            swimlane.wip_limit = int(self.field_value)
        elif self.field_name in ('target_start', 'target_end'):
            if not self.field_value.strip():
                setattr(swimlane, self.field_name, None)
            else:
                dt = datetime.datetime.fromisoformat(self.field_value)
                setattr(swimlane, self.field_name, dt)
        else:
            setattr(swimlane, self.field_name, self.field_value)
        session.commit()
        return swimlane


class AddEntry(Action):
    __tablename__ = 'add_entry'
    __mapper_args__ = {'polymorphic_identity': 'add_entry'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)
    parent_id = Column(Integer, ForeignKey('entries.id_'))
    insertion_index = Column(Integer)
    text = Column(Unicode)

    def __init__(self, parent, insertion_index: int, text: str):
        if isinstance(parent, EntryState):
            self.parent_id = parent.id_
        elif parent is None:
            self.parent_id = None
        else:
            raise TypeError(
                'parent parameter must be an EntryState instance or None.'
            )
        self.insertion_index = insertion_index
        self.text = text

    def validate(self, session: Session):
        if session.query(EntryState).filter(
            EntryState.id_ == self.entry_id
        ).count():
            raise IndexError(
                f'Column with id == {self.entry_id} already exists.'
            )
        if self.parent_id is not None:
            try:
                session.query(EntryState).filter(
                    EntryState.id_ == self.parent_id
                ).one()
            except NoResultFound:
                raise IndexError(f'Parent entry not found : {self.parent_id}.')
            sibling_count = session.query(EntryState).filter(
                and_(EntryState.branch_id == self.parent_id,
                     EntryState.status != 'removed'
                     )
            ).count()
            if (self.insertion_index > sibling_count
                    or self.insertion_index < -1):
                raise IndexError(
                    f'Invalid insertion index : {self.insertion_index}'
                )

    def apply(self, session: Session):
        self.validate(session)
        user = self.record_current_user(session)
        #  Adjust existing indices.
        for entry in session.query(EntryState).filter(
            and_(EntryState.branch_id == self.parent_id,
                 EntryState.status != 'removed')
        ).all():
            if entry.outline_index >= self.insertion_index:
                entry.outline_index += 1
        new_entry = EntryState(
            outline_index=self.insertion_index,
            text=self.text, branch_id=self.parent_id, status='note',
            created_by=user.uid, created_timestamp=datetime.datetime.now(),
            modified_by=user.uid, modified_timestamp=datetime.datetime.now(),
        )
        session.add(new_entry)
        session.commit()
        self.entry_id = new_entry.id_
        session.commit()
        return new_entry


class RemoveEntry(Action):
    __tablename__ = 'remove_entry'
    __mapper_args__ = {'polymorphic_identity': 'remove_entry'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)

    def __init__(self, entry: EntryState):
        self.entry_id = entry.id_

    def validate(self, session: Session):
        # Raises an exception if the column_id isn't found.
        session.query(EntryState).filter(EntryState.id_ == self.entry_id).one()

    @staticmethod
    def _mark_entries_removed(entry):
        entry.status = 'removed'
        for twig in entry.twigs:
            RemoveEntry._mark_entries_removed(twig)

    def apply(self, session: Session):
        self.validate(session)
        self.record_current_user(session)
        entry = session.query(EntryState)\
            .filter(EntryState.id_ == self.entry_id).one()
        RemoveEntry._mark_entries_removed(entry)
        session.commit()


class ModifyEntry(Action):
    __tablename__ = 'modify_entry'
    __mapper_args__ = {'polymorphic_identity': 'modify_entry'}
    id_ = Column(Integer, ForeignKey('actions.id_'), primary_key=True)

    def validate(self, session: Session):
        pass

    def apply(self, session: Session):
        self.validate(session)
        self.record_current_user(session)


class MoveEntryOnBoard(Action):
    pass


class MoveEntryOnOutline(Action):
    pass
