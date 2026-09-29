DROP VIEW IF EXISTS fraud_transactions;

CREATE VIEW fraud_transactions AS
SELECT
    t.id AS transaction_id,
    t.date AS transaction_datetime,
    DATE(t.date) AS transaction_date,
    CAST(strftime('%H', t.date) AS INTEGER) AS transaction_hour,
    CAST(strftime('%w', t.date) AS INTEGER) AS transaction_day_of_week,
    t.amount_usd_cents,
    t.amount_usd_cents / 100.0 AS amount_usd,
    t.transaction_type,
    t.errors,
    c.id AS card_id,
    c.card_brand,
    c.card_type,
    c.has_chip,
    c.credit_limit_usd_cents,
    c.credit_limit_usd_cents / 100.0 AS credit_limit_usd,
    c.acct_open_date,
    CAST(julianday(t.date) - julianday(c.acct_open_date) AS INTEGER)
        AS card_age_days,
    c.year_pin_last_changed,
    c.card_on_dark_web,
    u.id AS user_id,
    u.birth_date,
    CAST(
        (julianday(t.date) - julianday(u.birth_date)) / 365.25
        AS INTEGER
    ) AS user_age_years,
    u.gender,
    u.per_capita_income_usd_cents,
    u.per_capita_income_usd_cents / 100.0 AS per_capita_income_usd,
    u.yearly_income_usd_cents,
    u.yearly_income_usd_cents / 100.0 AS yearly_income_usd,
    u.total_debt_usd_cents,
    u.total_debt_usd_cents / 100.0 AS total_debt_usd,
    u.credit_score,
    CASE
        WHEN c.credit_limit_usd_cents > 0
        THEN CAST(t.amount_usd_cents AS REAL) / c.credit_limit_usd_cents
        ELSE NULL
    END AS amount_to_credit_limit_ratio,
    m.id AS merchant_id,
    m.name AS merchant_name,
    m.mcc,
    mc.description AS merchant_category,
    ml.id AS merchant_location_id,
    ml.city AS merchant_city,
    ml.state AS merchant_state,
    ml.zip AS merchant_zip,
    fl.is_fraud
FROM transactions AS t
LEFT JOIN cards AS c
    ON t.card_id = c.id
LEFT JOIN users AS u
    ON c.user_id = u.id
LEFT JOIN merchants AS m
    ON t.merchant_id = m.id
LEFT JOIN mcc_codes AS mc
    ON m.mcc = mc.mcc
LEFT JOIN merchant_locations AS ml
    ON t.merchant_location_id = ml.id
LEFT JOIN fraud_labels AS fl
    ON CAST(t.id AS TEXT) = fl.transaction_id;
