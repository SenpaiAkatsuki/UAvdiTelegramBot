from aiogram.filters.callback_data import CallbackData

"""
Application voting callback schema.

Encodes application id and vote decision for inline admin voting buttons.
"""

VOTE_DECISION_APPROVE = "approve"
VOTE_DECISION_REJECT = "reject"


class ApplicationVoteCallbackData(CallbackData, prefix="appvote"):
    # Callback payload for one-message admin vote actions.
    application_id: int
    decision: str
