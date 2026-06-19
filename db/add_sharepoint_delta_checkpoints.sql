ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_next_link TEXT;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_page INTEGER;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_items_seen BIGINT;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_updated_at TIMESTAMPTZ;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS last_error_page INTEGER;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS last_error_message TEXT;
