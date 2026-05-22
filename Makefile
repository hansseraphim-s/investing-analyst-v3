.PHONY: install test agent-test agent-lint dashboard-dev dashboard-build backtest paper walk-forward clean

install:
	cd agent && pip install -e ".[dev]"
	cd dashboard && pnpm install

test: agent-test

agent-test:
	cd agent && pytest -q

agent-lint:
	cd agent && ruff check src tests

dashboard-dev:
	cd dashboard && pnpm dev

dashboard-build:
	cd dashboard && pnpm build

backtest:
	cd agent && iav3 backtest --symbols AAPL,MSFT,NVDA,SPY --strategy vol_target_trend --start 2015-01-01

paper:
	cd agent && iav3 paper --strategy vol_target_trend

walk-forward:
	cd agent && iav3 walk-forward --strategy vol_target_trend --symbols AAPL,MSFT,NVDA,SPY --start 2015-01-01

db-migrate:
	psql "$$NEON_DATABASE_URL" -f shared/schema.sql

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf agent/build agent/dist agent/*.egg-info
	rm -rf dashboard/.next
