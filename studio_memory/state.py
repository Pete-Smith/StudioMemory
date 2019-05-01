import sys

import PyQt5.QtCore as core
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Integer, Unicode, Boolean, String
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
    _column_types = ('queue', 'step', 'breakdown', 'collect')
    column_type = Column(
        Enum(*_column_types, name='ColumnType'),
        default='queue'
    )
    wip_limit = Column(Integer, default=0)
    line_of_commitment = Column(Boolean)
    status = Column(
        Enum('active', 'removed', name='ColumnStatus'), default='active'
    )

    def __repr__(self):
        return (
            f"<ColumnState(id_={self.id_}, title='{self.title}', "
            f"done_rule='{self.done_rule}', column_type='{self.column_type}', "
            f"wip_limit={self.wip_limit}, status='{self.status}')>"
        )


class SwimlaneState(DeclarativeBase):
    __tablename__ = 'swimlanes'
    id_ = Column(Integer, primary_key=True)
    title = Column(Unicode)
    wip_limit = Column(Integer)
    status = Column(
        Enum('active', 'removed', name='ColumnStatus'), default='active'
    )
    target_start = Column(DateTime)
    target_end = Column(DateTime)

    def __repr__(self):
        return(
            f"<SwimlaneState(id_={self.id_}, board_index={self.board_index}, "
            f"title='{self.title}', wip_limit={self.wip_limit}, "
            f"status='{self.status}', target_start={self.target_start}, "
            f"target_end={self.target_end})>"
        )


class EntryState(DeclarativeBase):
    __tablename__ = 'entries'
    id_ = Column(Integer, primary_key=True)
    outline_index = Column(Integer)
    board_index = Column(Integer)
    subcolumn_index = Column(Integer, default=0)
    swimlane_id = Column(Integer, ForeignKey('swimlanes.id_'))
    column_id = Column(Integer, ForeignKey('columns.id_'))
    branch_id = Column(Integer, ForeignKey('entries.id_'))
    text = Column(Unicode)
    level = Column(Integer)
    inception = Column(DateTime)
    cycle_start = Column(DateTime)
    cycle_end = Column(DateTime)
    status = Column(
        Enum(
            'note', # Not on the board, simply an outline entry.
            'card', # An active card on the board
            'blocked', # A card on the board that a User has flagged as 'blocked'
            'discarded', # A card that has been discarded
            'complete', # A card that is in the last space on the board
            'removed', # An entry that has been deleted from the outline entirely.
             name='StatusType'),
        default='note'
    )


class User(DeclarativeBase):
    __tablename__ = 'users'
    uid = Column(String(36), primary_key=True)
    name = Column(Unicode)
    @staticmethod
    def check_in(name:str, uid:bytes):
        """
        Gets or creates a User and returns the instance.
        User names for identifying edits, and not keeping people from
        reading our data.
        Raises an exception if the name and uid do not match.
        """
        app = core.QCoreApplication.instance() or core.QCoreApplication(sys.argv)
        settings = core.QSettings(ORG_NAME, APP_NAME)
        settings.setValue('User.name', name)
        settings.setValue('User.uid', uid)

    @staticmethod
    def current(session:Session):
        """
        Retrieve the checked-in User instance.
        If the User doesn't exist in the sessions database,
        and there isn't a conflict, this method will create it.
        """
        app = core.QCoreApplication.instance() or core.QCoreApplication(sys.argv)
        settings = core.QSettings(ORG_NAME, APP_NAME)
        name = settings.value('User.name', None)
        uid = settings.value('User.uid', None)
        if None in (name, uid):
            raise EnvironmentError('No user is currently checked-in.')
        try:
            user = session.query(User)\
                    .filter(User.name.ilike(name), User.uid==uid).one()
        except NoResultFound:
            same_name = session.query(User)\
                    .filter(User.name.ilike(name)).count()
            same_uid = session.query(User).filter(User.uid==uid).count()
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


ColumnState.entries = relationship('EntryState', back_populates='column')
SwimlaneState.entries = relationship(
    'EntryState', back_populates='swimlane', order_by='EntryState.board_index'
)
EntryState.swimlane = relationship('SwimlaneState', back_populates='entries')
EntryState.column = relationship(
    'ColumnState', back_populates='entries', order_by='EntryState.board_index'
)
EntryState.branch = relationship(
    'EntryState', remote_side=[EntryState.id_], backref='twigs', order_by='EntryState.outline_index'
)
