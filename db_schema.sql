-- Stores each WhatsApp chat session (per user/wa_id + client)
CREATE TABLE IF NOT EXISTS conversations (
  id SERIAL PRIMARY KEY,
  client_name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'whatsapp',
  user_id TEXT NOT NULL,              -- wa_id
  status TEXT NOT NULL DEFAULT 'open', -- open / closed
  topic TEXT DEFAULT '',
  last_intent TEXT DEFAULT '',
  missing_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_user_message TEXT DEFAULT '',
  last_bot_message TEXT DEFAULT '',
  turns INT NOT NULL DEFAULT 0,
  updated_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (client_name, channel, user_id)
);

-- Stores message history (for audit + context)
CREATE TABLE IF NOT EXISTS conversation_messages (
  id SERIAL PRIMARY KEY,
  conversation_id INT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                 -- user / bot / system
  text TEXT NOT NULL,
  created_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_utc DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv_id ON conversation_messages(conversation_id, created_utc DESC);
