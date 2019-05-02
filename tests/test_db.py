import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid

import studio_memory.actions as actions
import studio_memory.state as state
from studio_memory import DeclarativeBase


@pytest.fixture
def session():
    """ Set-up a blank sqlite session in memory.  """
    engine = create_engine('sqlite:///:memory:', echo=False)
    DeclarativeBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


@pytest.fixture
def basic_board(session):
    """ Set-up a basic three column kanban board.  """
    state.User.check_in(name="Test User", uid=str(uuid.uuid4()).encode('ascii'))
    wip_limits = (0, 1, 0)
    for i, title in enumerate(('Pending', 'Doing', 'Done')):
        add_column_action = actions.AddColumn(i)
        session.add(add_column_action)
        column = add_column_action.apply(session)
        modify_title_action = actions.ModifyColumn(column, 'title', title)
        session.add(modify_title_action)
        column = modify_title_action.apply(session)
        modify_wip_limit_action = actions.ModifyColumn(
            column, 'wip_limit', wip_limits[i]
        )
        session.add(modify_wip_limit_action)
        modify_wip_limit_action.apply(session)
    return session


def test_column_title_assignment(basic_board):
    """
    Test that the column titles assigned in the basic_board fixture persisted.
    """
    pending_column = basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Pending').one()
    doing_column = basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Doing').one()
    done_column = basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Done').one()


def test_simple_column_move(basic_board):
    """ Test if we can move some of the columns on the basic_board around. """
    done_column = basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Done').one()
    move_column_action = actions.MoveColumn(done_column.id_, 0)
    move_column_action.apply(basic_board)
    # Check that the column indices stayed in order.
    column_indices = [
        c.board_index for c in
        basic_board.query(state.ColumnState)\
            .order_by(state.ColumnState.board_index)
    ]
    assert column_indices == list(range(len(column_indices)))






