"""Database-level invariants: immutable published recipe versions, append-only stock ledger.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_food_name_trgm ON food USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_name_trgm ON product USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_recipe_title_trgm ON recipe USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cofid_name_trgm ON cofid_food USING gin (name gin_trgm_ops)")

    # A published recipe version is immutable. Only deletion of the whole recipe may remove it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION larder_protect_published_version() RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'published' THEN
            -- FK SET NULL from a deleted parent version or user is the only permitted change
            IF (to_jsonb(NEW) - 'parent_version_id' - 'created_by_id' - 'updated_at')
               = (to_jsonb(OLD) - 'parent_version_id' - 'created_by_id' - 'updated_at') THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'recipe_version % is published and immutable', OLD.id USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_protect_published_version BEFORE UPDATE ON recipe_version "
        "FOR EACH ROW EXECUTE FUNCTION larder_protect_published_version()"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION larder_protect_published_children() RETURNS trigger AS $$
        DECLARE st text; vid uuid;
        BEGIN
          IF TG_OP = 'DELETE' THEN vid := OLD.version_id; ELSE vid := NEW.version_id; END IF;
          SELECT status INTO st FROM recipe_version WHERE id = vid;
          IF st = 'published' THEN
            -- FK SET NULL when a linked food/product is deleted keeps the source wording intact
            IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'recipe_ingredient'
               AND (to_jsonb(NEW) - 'food_id' - 'product_id') = (to_jsonb(OLD) - 'food_id' - 'product_id') THEN
              RETURN NEW;
            END IF;
            -- cascading delete of the whole recipe is allowed (parent row already gone or being deleted)
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN RETURN OLD; END IF;
            RAISE EXCEPTION '% rows of published recipe_version % are immutable', TG_TABLE_NAME, vid
              USING ERRCODE = 'check_violation';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    for table in ("recipe_ingredient", "recipe_step"):
        op.execute(
            f"CREATE TRIGGER trg_protect_{table} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION larder_protect_published_children()"
        )

    # The stock ledger is append-only.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION larder_stock_event_append_only() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            -- FK SET NULL from deleted batches/receipt lines is the only permitted change
            IF (to_jsonb(NEW) - 'cooked_batch_id' - 'receipt_line_id' - 'actor_user_id')
               = (to_jsonb(OLD) - 'cooked_batch_id' - 'receipt_line_id' - 'actor_user_id') THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'stock_event rows are append-only' USING ERRCODE = 'check_violation';
          END IF;
          IF pg_trigger_depth() > 1 THEN RETURN OLD; END IF;  -- household/lot cascade delete
          RAISE EXCEPTION 'stock_event rows are append-only' USING ERRCODE = 'check_violation';
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_stock_event_append_only BEFORE UPDATE OR DELETE ON stock_event "
        "FOR EACH ROW EXECUTE FUNCTION larder_stock_event_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_stock_event_append_only ON stock_event")
    op.execute("DROP FUNCTION IF EXISTS larder_stock_event_append_only")
    for table in ("recipe_ingredient", "recipe_step"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_protect_{table} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS larder_protect_published_children")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_published_version ON recipe_version")
    op.execute("DROP FUNCTION IF EXISTS larder_protect_published_version")
    for idx in ("ix_food_name_trgm", "ix_product_name_trgm", "ix_recipe_title_trgm", "ix_cofid_name_trgm"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
