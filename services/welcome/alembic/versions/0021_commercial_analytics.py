"""commercial analytics, campaigns and six-month referrals

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE plan ADD COLUMN calculation_price_rub NUMERIC(14,2)")
    op.execute(
        "UPDATE plan SET calculation_price_rub = COALESCE(price_rub, referral_base_rub) "
        "WHERE slug = 'business'"
    )
    op.execute(
        "UPDATE referral_attribution "
        "SET commission_ends_at = first_paid_at + INTERVAL '6 months' "
        "WHERE first_paid_at IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE subscription_reminder DROP CONSTRAINT ck_subscription_reminder_day"
    )
    op.execute("UPDATE subscription_reminder SET days_before = 5 WHERE days_before = 7")
    op.execute(
        "ALTER TABLE subscription_reminder ADD CONSTRAINT ck_subscription_reminder_day "
        "CHECK (days_before IN (5,3,1))"
    )

    op.execute(
        """
        CREATE TABLE marketing_campaign (
          id BIGSERIAL PRIMARY KEY,
          public_token UUID NOT NULL,
          name VARCHAR(128) NOT NULL,
          starts_at TIMESTAMPTZ NOT NULL,
          ends_at TIMESTAMPTZ,
          attribution_window_days INTEGER NOT NULL DEFAULT 30,
          is_active BOOLEAN NOT NULL DEFAULT true,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT marketing_campaign_public_token_key UNIQUE(public_token),
          CONSTRAINT ck_campaign_window CHECK (attribution_window_days BETWEEN 1 AND 365),
          CONSTRAINT ck_campaign_period CHECK (ends_at IS NULL OR ends_at > starts_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_marketing_campaign_public_token ON marketing_campaign(public_token)")
    op.execute("CREATE INDEX ix_marketing_campaign_active ON marketing_campaign(is_active, starts_at, ends_at)")
    op.execute(
        """
        CREATE TABLE campaign_touch (
          id BIGSERIAL PRIMARY KEY,
          campaign_id BIGINT NOT NULL REFERENCES marketing_campaign(id) ON DELETE CASCADE,
          owner_id BIGINT NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
          click_count INTEGER NOT NULL DEFAULT 1,
          is_new_free BOOLEAN NOT NULL DEFAULT false,
          first_clicked_at TIMESTAMPTZ NOT NULL,
          last_clicked_at TIMESTAMPTZ NOT NULL,
          CONSTRAINT uq_campaign_touch_owner UNIQUE(campaign_id, owner_id),
          CONSTRAINT ck_campaign_touch_click_count CHECK (click_count > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_campaign_touch_campaign_id ON campaign_touch(campaign_id)")
    op.execute("CREATE INDEX ix_campaign_touch_owner_id ON campaign_touch(owner_id)")
    op.execute("CREATE INDEX ix_campaign_touch_last_clicked_at ON campaign_touch(last_clicked_at)")
    op.execute(
        """
        CREATE TABLE campaign_attribution (
          id BIGSERIAL PRIMARY KEY,
          campaign_id BIGINT NOT NULL REFERENCES marketing_campaign(id) ON DELETE RESTRICT,
          owner_id BIGINT NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
          attributed_at TIMESTAMPTZ NOT NULL,
          CONSTRAINT campaign_attribution_owner_id_key UNIQUE(owner_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_campaign_attribution_campaign_id ON campaign_attribution(campaign_id)")
    op.execute("CREATE INDEX ix_campaign_attribution_owner_id ON campaign_attribution(owner_id)")
    op.execute(
        """
        CREATE TABLE campaign_payment (
          id BIGSERIAL PRIMARY KEY,
          campaign_id BIGINT NOT NULL REFERENCES marketing_campaign(id) ON DELETE RESTRICT,
          owner_id BIGINT NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
          payment_id BIGINT NOT NULL REFERENCES payment(id) ON DELETE RESTRICT,
          amount_rub NUMERIC(14,2) NOT NULL,
          paid_at TIMESTAMPTZ NOT NULL,
          CONSTRAINT campaign_payment_payment_id_key UNIQUE(payment_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_campaign_payment_campaign_id ON campaign_payment(campaign_id)")
    op.execute("CREATE INDEX ix_campaign_payment_owner_id ON campaign_payment(owner_id)")
    op.execute("CREATE INDEX ix_campaign_payment_paid_at ON campaign_payment(paid_at)")
    op.execute(
        """
        CREATE TABLE weekly_business_snapshot (
          id BIGSERIAL PRIMARY KEY,
          week_start DATE NOT NULL,
          calculation_price_rub NUMERIC(14,2) NOT NULL,
          active_paid_clients INTEGER NOT NULL,
          first_paid_30d INTEGER NOT NULL,
          churned_7d INTEGER NOT NULL,
          weekly_revenue_run_rate_rub NUMERIC(14,2) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT weekly_business_snapshot_week_key UNIQUE(week_start)
        )
        """
    )
    op.execute("CREATE INDEX ix_weekly_business_snapshot_week_start ON weekly_business_snapshot(week_start)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weekly_business_snapshot")
    op.execute("DROP TABLE IF EXISTS campaign_payment")
    op.execute("DROP TABLE IF EXISTS campaign_attribution")
    op.execute("DROP TABLE IF EXISTS campaign_touch")
    op.execute("DROP TABLE IF EXISTS marketing_campaign")
    op.execute(
        "ALTER TABLE subscription_reminder DROP CONSTRAINT ck_subscription_reminder_day"
    )
    op.execute("UPDATE subscription_reminder SET days_before = 7 WHERE days_before = 5")
    op.execute(
        "ALTER TABLE subscription_reminder ADD CONSTRAINT ck_subscription_reminder_day "
        "CHECK (days_before IN (7,3,1))"
    )
    op.execute(
        "UPDATE referral_attribution "
        "SET commission_ends_at = first_paid_at + INTERVAL '12 months' "
        "WHERE first_paid_at IS NOT NULL"
    )
    op.execute("ALTER TABLE plan DROP COLUMN calculation_price_rub")
