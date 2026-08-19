"""owner bot premium emoji theme

Revision ID: 0014
Revises: 0013
"""

import json

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

PREMIUM_EMOJI = {
    "home": "5222048081469540579",
    "bot": "5222034552322555814",
    "channel": "5222223195876135675",
    "message": "5222151809224705882",
    "subscription": "5221994849644870178",
    "analytics": "5222129067372874693",
    "referral": "5827865545126448742",
    "help": "5222179494583894918",
    "guide": "5221944735966460787",
    "settings": "5224533196791645119",
    "add": "5222166223134948810",
    "edit": "5224638956066340457",
    "preview": "5222464169311241162",
    "publish": "5221929261199296089",
    "media": "5222289441451705159",
    "timer": "5222059742305747580",
    "success": "5224380798467080414",
    "warning": "5938188982784363983",
    "error": "5936119293878996291",
    "delete": "5936274861889424782",
    "back": "5935952739342224399",
    "next": "5936069167315684574",
    "copy": "5938090945860865210",
    "rotation": "5222301445885298017",
    "requests": "5224187211406151308",
    "important": "5221929261199296089",
}


def upgrade() -> None:
    config = json.dumps({"premium_emoji": PREMIUM_EMOJI}, separators=(",", ":"))
    op.execute(
        "UPDATE feature_flag "
        "SET enabled = true, config = " + "'" + config.replace("'", "''") + "'::jsonb, description = "
        "'Calm owner-bot controls with semantic Premium Emoji icons' "
        "WHERE key = 'bot_inline_ui'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE feature_flag SET config = "
        '\'{"premium_emoji":{"help":"5368324170671202286",'
        '"guide":"5368324170671202286",'
        '"important":"5368324170671202286"}}\'::jsonb '
        "WHERE key = 'bot_inline_ui'"
    )
