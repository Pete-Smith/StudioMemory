""" Objects that persist the cumulative result of the action classes. """
import sys

from PyQt5 import QtCore
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Integer, Unicode, Boolean, String, and_,
)
from sqlalchemy.orm import relationship
from sqlalchemy.orm.session import Session
from sqlalchemy.orm.exc import NoResultFound

from studio_memory import DeclarativeBase
from studio_memory import APP_NAME, ORG_NAME


class ColumnState(DeclarativeBase):
    __tablename__ = 'columns'
    id_ = Column(Integer, primary_key=True)
    board_index = Column(Integer)
    title = Column(Unicode, default='')
    done_rule = Column(Unicode, default='')
    column_types = ('queue', 'step', 'breakdown', 'collect')
    column_type = Column(
        Enum(*column_types, name='ColumnType'),
        default='queue'
    )
    wip_limit = Column(Integer, default=0)
    line_of_commitment = Column(Boolean, default=False)
    status = Column(
        Enum('active', 'removed', name='ColumnStatus'), default='active'
    )

    def __repr__(self):
        return (
            f"<ColumnState(id_={self.id_}, title='{self.title}', "
            f"done_rule='{self.done_rule}', column_type='{self.column_type}', "
            f"wip_limit={self.wip_limit}, status='{self.status}')>"
        )

    @staticmethod
    def active_columns(session: Session):
        return [
            c for c in
            session.query(ColumnState).filter(ColumnState.status == 'active')
            .order_by(ColumnState.board_index)
        ]

    def active_card_count(self):
        session = Session.object_session(self)
        return session.query(EntryState).filter(and_(
            EntryState.column == self,
            ~EntryState.status.in_(['discarded', 'removed'])
        )).count()


class SwimlaneState(DeclarativeBase):
    __tablename__ = 'swimlanes'
    id_ = Column(Integer, primary_key=True)
    title = Column(Unicode)
    wip_limit = Column(Integer)
    status = Column(
        Enum('active', 'removed', name='ColumnStatus'), default='active'
    )
    target_start = Column(DateTime, nullable=True)
    target_end = Column(DateTime, nullable=True)

    def __repr__(self):
        return(
            f"<SwimlaneState(id_={self.id_}, board_index={self.board_index}, "
            f"title='{self.title}', wip_limit={self.wip_limit}, "
            f"status='{self.status}', target_start={self.target_start}, "
            f"target_end={self.target_end}>"
        )

    def active_card_count(self):
        session = Session.object_session(self)
        return session.query(EntryState).filter(and_(
            EntryState.swimlane == self,
            ~EntryState.status.in_(['discarded', 'removed'])
        )).count()


class EntryState(DeclarativeBase):
    __tablename__ = 'entries'
    id_ = Column(Integer, primary_key=True)
    swimlane_id = Column(Integer, ForeignKey('swimlanes.id_'))
    column_id = Column(Integer, ForeignKey('columns.id_'))
    branch_id = Column(Integer, ForeignKey('entries.id_'))
    # Outline sorting, per branch
    outline_index = Column(Integer)
    # Board sorting, per column-swimlane box.
    board_index = Column(Integer)
    # Some column-swimlane boxes are partitioned.
    # Like the doing and done sub-steps in the step columns.
    subcolumn_index = Column(Integer, default=0)
    text = Column(Unicode)
    # What's level for? Uncomment this when I figure out what I was thinking.
    # level = Column(Integer)
    cycle_start = Column(DateTime, nullable=True)
    cycle_end = Column(DateTime, nullable=True)
    created_by = Column(String(36), ForeignKey('users.uid'))
    created_timestamp = Column(DateTime, nullable=True)
    modified_by = Column(String(36), ForeignKey('users.uid'))
    modified_timestamp = Column(DateTime, nullable=True)
    status = Column(
        Enum(
            # Not on the board, simply an outline entry.
            'note',
            # An active card on the board
            'card',
            # A card on the board that a User has flagged as 'blocked'
            'blocked',
            # A card that has been discarded.
            # Treated like a note, but has a history on the board.
            'discarded',
            # A card that is in the last space on the board
            'complete',
            # An entry that has been deleted from the outline entirely.
            'removed',
            name='StatusType'
        ),
        default='note'
    )


class User(DeclarativeBase):
    __tablename__ = 'users'
    uid = Column(String(36), primary_key=True)
    name = Column(Unicode)
    @staticmethod
    def check_in(name: str, uid: bytes):
        """
        Gets or creates a User and returns the instance.
        User names for identifying edits, and not keeping people from
        reading our data.
        Raises an exception if the name and uid do not match.
        """
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(sys.argv)
        settings = QtCore.QSettings(ORG_NAME, APP_NAME)
        settings.setValue('User.name', name)
        settings.setValue('User.uid', uid)

    @staticmethod
    def current(session: Session):
        """
        Retrieve the checked-in User instance.
        If the User doesn't exist in the sessions database,
        and there isn't a conflict, this method will create it.
        """
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(sys.argv)
        settings = QtCore.QSettings(ORG_NAME, APP_NAME)
        name = settings.value('User.name', None)
        uid = settings.value('User.uid', None)
        if None in (name, uid):
            raise EnvironmentError('No user is currently checked-in.')
        try:
            user = session.query(User)\
                    .filter(User.name.ilike(name), User.uid == uid).one()
        except NoResultFound:
            same_name = session.query(User)\
                    .filter(User.name.ilike(name)).count()
            same_uid = session.query(User).filter(User.uid == uid).count()
            if same_name or same_uid:
                raise ValueError(
                    f'User name/uid mismatch.\n'
                    f'{same_name} user{"s" if same_name != 1 else ""} have the '
                    f'same name : {name}\n'
                    f'{same_uid} user{"s" if same_uid != 1 else ""} have the '
                    f'same unique id : {uid}'
                )
            user = User(name=name, uid=uid)
            session.add(user)
            session.commit()
        return user


ColumnState.entries = relationship('EntryState', back_populates='column')
SwimlaneState.entries = relationship(
    'EntryState', back_populates='swimlane', order_by='EntryState.board_index'
)
EntryState.swimlane = relationship('SwimlaneState', back_populates='entries')
EntryState.column = relationship(
    'ColumnState', back_populates='entries', order_by='EntryState.board_index'
)
EntryState.branch = relationship(
    'EntryState', remote_side=[EntryState.id_], backref='twigs',
    order_by='EntryState.outline_index'
)
