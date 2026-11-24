from app.rework import (
    RecoveryDesk,
    RecoveryLedger,
    ReworkBook,
    ReworkDesk,
    ReworkLedger,
    Workbench,
    create_desk,
)

__all__ = [
    'RecoveryDesk', 'RecoveryLedger', 'ReworkBook', 'ReworkDesk', 'ReworkLedger',
    'Workbench', 'create_desk', 'board', 'desk', 'ledger',
    'register_recovery', 'submit_recovery', 'verify_remaining',
]

board = Workbench()
desk = board
ledger = board


def register_recovery(*args, **kwargs):
    return board.register_recovery(*args, **kwargs)


def submit_recovery(*args, **kwargs):
    return board.submit_recovery(*args, **kwargs)


def verify_remaining(*args, **kwargs):
    return board.verify_remaining(*args, **kwargs)
