"""replace owner bot Apple icons with a Telegram-native icon theme

Revision ID: 0015
Revises: 0014
"""

import json

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

PREMIUM_EMOJI = {
    "home": "5974453749601537448",
    "bot": "5971808079811972376",
    "channel": "5783105032350076195",
    "message": "5974490089319828950",
    "subscription": "5976377521287990495",
    "analytics": "5974047364090957805",
    "referral": "5974492756494519709",
    "help": "6001517450930163276",
    "guide": "5974290527959386992",
    "settings": "5974104203688152439",
    "add": "5971860323794160759",
    "edit": "6010548023396928773",
    "preview": "5974350313904147369",
    "publish": "5974192980662160632",
    "media": "5974563790958627920",
    "timer": "5974585609392492550",
    "success": "6008275560495582704",
    "warning": "5976801477509778431",
    "error": "5972201876773408053",
    "delete": "5974518878485615140",
    "back": "5854967531793550989",
    "next": "5974249837439224721",
    "copy": "5974434516737985904",
    "rotation": "6010590938710152619",
    "requests": "5775973900580031963",
    "important": "5972187557352443077",
}


def upgrade() -> None:
    config = json.dumps({"premium_emoji": PREMIUM_EMOJI}, separators=(",", ":"))
    op.execute(
        "UPDATE feature_flag SET config = "
        + "'"
        + config.replace("'", "''")
        + "'::jsonb, description = "
        + "'Calm owner-bot controls with Telegram-native semantic icons' "
        + "WHERE key = 'bot_inline_ui'"
    )


def downgrade() -> None:
    # The application defaults remain safe even when runtime customization is
    # removed; avoiding a copy of the retired theme keeps rollback predictable.
    op.execute("UPDATE feature_flag SET config = '{}'::jsonb WHERE key = 'bot_inline_ui'")
