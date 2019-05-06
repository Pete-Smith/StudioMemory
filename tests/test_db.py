""" Test the state and action database system. """
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid

import studio_memory.actions as actions
import studio_memory.state as state
from studio_memory import DeclarativeBase

BASIC_WIP_LIMITS = (0, 1, 0)


@pytest.fixture
def session():
    """ Set-up a blank sqlite session in memory.  """
    engine = create_engine('sqlite:///:memory:', echo=False)
    DeclarativeBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    return session()


@pytest.fixture
def basic_board(session):
    """ Set-up a basic three column kanban board.  """
    state.User.check_in(name="Test User", uid=str(uuid.uuid4()).encode('ascii'))
    for i, title in enumerate(('Pending', 'Doing', 'Done')):
        add_column_action = actions.AddColumn(i)
        session.add(add_column_action)
        column = add_column_action.apply(session)
        modify_title_action = actions.ModifyColumn(column, 'title', title)
        session.add(modify_title_action)
        session.query(state.ColumnState)\
            .filter(state.ColumnState.id_ == modify_title_action.column_id).one()
        column = modify_title_action.apply(session)
        modify_wip_limit_action = actions.ModifyColumn(
            column, 'wip_limit', BASIC_WIP_LIMITS[i]
        )
        session.add(modify_wip_limit_action)
        modify_wip_limit_action.apply(session)
    return session


def test_column_title_assignment(basic_board):
    """
    Test that the column titles assigned in the basic_board fixture persisted.
    """
    basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Pending').one()
    basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Doing').one()
    basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Done').one()


def test_wip_limit_assignment(basic_board):
    """ Test that the column wip limits in the basic_board fixture stuck. """
    wip_limits = tuple([
        c.wip_limit for c in
        basic_board.query(state.ColumnState)
        .order_by(state.ColumnState.board_index)
    ])
    assert BASIC_WIP_LIMITS == wip_limits


def test_simple_column_move(basic_board):
    """ Test if we can move some of the columns on the basic_board around. """
    done_column = basic_board.query(state.ColumnState)\
        .filter(state.ColumnState.title == 'Done').one()
    move_column_action = actions.MoveColumn(done_column, 0)
    move_column_action.apply(basic_board)
    # Check that the column indices stayed in order.
    column_indices = [
        c.board_index for c in
        basic_board.query(state.ColumnState)
        .order_by(state.ColumnState.board_index)
    ]
    assert column_indices == list(range(len(column_indices)))


def test_column_removal(basic_board):
    """ Test removing a column from the basic_board. """
    column = basic_board.query(state.ColumnState) \
        .filter(state.ColumnState.title == 'Pending').one()
    remove_column_action = actions.RemoveColumn(column.id_)
    remove_column_action.apply(basic_board)
    active_columns = state.ColumnState.active_columns(basic_board)
    assert column.status == 'removed'
    assert len(active_columns) == 2
    # Test that the board_indices weren't touched.
    assert [1, 2] == [c.board_index for c in active_columns]


def test_disallowed_column_changes(basic_board):
    """ Try changing the basic_board fixture in disallowed ways. """
    done_column = basic_board.query(state.ColumnState) \
        .filter(state.ColumnState.title == 'Done').one()
    with pytest.raises(ValueError):
        bad_rename = actions.ModifyColumn(done_column, 'title', 'Pending')
        bad_rename.apply(basic_board)
    with pytest.raises(ValueError):
        invalid_wip = actions.ModifyColumn(done_column, 'wip_limit', '-1')
        invalid_wip.apply(basic_board)
    with pytest.raises(AttributeError):
        misspelled = actions.ModifyColumn(done_column, 'tootle', 'Title')
        misspelled.apply(basic_board)


def test_swimlane_creation(basic_board):
    """
    Test adding a swimlane to the basic board.
    Test the prohibition of duplicate titles and negative WIP limits.
    """
    add_swimlane_action = actions.AddSwimlane('Test Title', 0)
    add_swimlane_action.apply(basic_board)
    basic_board.query(state.SwimlaneState)\
        .filter(state.SwimlaneState.id_ == add_swimlane_action.swimlane_id).one()
    # Duplicate titles are disallowed.
    with pytest.raises(ValueError):
        redundant_add = actions.AddSwimlane('Test Title', 0)
        redundant_add.apply(basic_board)
    # Negative WIP limits are disallowed.
    with pytest.raises(ValueError):
        malformed_add = actions.AddSwimlane('Impossible', -1)
        malformed_add.apply(basic_board)


def test_swimlane_removal(basic_board):
    add_swimlane_action = actions.AddSwimlane('Test Title', 0)
    swimlane = add_swimlane_action.apply(basic_board)
    remove_swimlane_action = actions.RemoveSwimlane(swimlane)
    remove_swimlane_action.apply(basic_board)


def test_swimlane_modification(basic_board):
    add_swimlane_action = actions.AddSwimlane('Test Title', 0)
    swimlane = add_swimlane_action.apply(basic_board)
    modify_wip = actions.ModifySwimlane(swimlane, 'wip_limit', 1)
    modify_wip.apply(basic_board)
    assert swimlane.wip_limit == 1
    modify_title = actions.ModifySwimlane(swimlane, 'title', 'Test Title')
    modify_title.apply(basic_board)
    assert swimlane.title == 'Test Title'
    timestamp = datetime.datetime.now().isoformat()
    modify_target_start = actions.ModifySwimlane(
        swimlane, 'target_start', timestamp
    )
    modify_target_start.apply(basic_board)
    assert swimlane.target_start == datetime.datetime.fromisoformat(timestamp)
    # Negative WIP limits are disallowed.
    with pytest.raises(ValueError):
        invalid_wip = actions.ModifySwimlane(swimlane, 'wip_limit', '-1')
        invalid_wip.apply(basic_board)
    # Non-integer WIP limits are disallowed.
    with pytest.raises(ValueError):
        invalid_wip = actions.ModifySwimlane(swimlane, 'wip_limit', 'foo')
        invalid_wip.apply(basic_board)
    # Empty titles are disallowed.
    with pytest.raises(ValueError):
        invalid_title = actions.ModifySwimlane(swimlane, 'title', '')
        invalid_title.apply(basic_board)


def test_basic_entry_creation(basic_board):
    add_entry_action = actions.AddEntry(None, 0, 'Test Entry')
    add_entry_action.apply(basic_board)
    basic_board.query(state.EntryState)\
        .filter(state.EntryState.id_ == add_entry_action.entry_id).one()
