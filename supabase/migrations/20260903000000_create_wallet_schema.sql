CREATE TYPE transaction_type AS ENUM ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER_IN', 'TRANSFER_OUT');
CREATE TYPE transaction_status AS ENUM ('SUCCESS', 'FAILED');

CREATE TABLE users (
	id UUID NOT NULL,
	username VARCHAR(50) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_username ON users (username);

CREATE TABLE wallets (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	balance NUMERIC(12, 2) NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_wallets_balance_non_negative CHECK (balance >= 0),
	UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE transactions (
	id UUID NOT NULL,
	wallet_id UUID NOT NULL,
	type transaction_type NOT NULL,
	amount NUMERIC(12, 2) NOT NULL,
	recipient_wallet_id UUID,
	status transaction_status NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(wallet_id) REFERENCES wallets (id) ON DELETE CASCADE,
	FOREIGN KEY(recipient_wallet_id) REFERENCES wallets (id) ON DELETE SET NULL
);

CREATE INDEX ix_transactions_wallet_created ON transactions (wallet_id, created_at);

-- Supabase auto-exposes every table over a public REST API. This app never uses
-- it (it connects over Postgres directly), so RLS with no policies blocks that
-- API entirely. The table owner bypasses RLS, so the app is unaffected.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
