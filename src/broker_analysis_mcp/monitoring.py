"""One portfolio analysis per invocation, suitable for cron or Task Scheduler.

Exit codes: 0 completed, 2 input/analysis failure, 3 completed with coverage_alarm.
Successful analyses (including alarms) emit JSON on stdout; errors emit JSON on stderr.
Scheduling and notifications are the caller's responsibility.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import portfolio
from .models import Position, StressScenario


class MonitorRequest(BaseModel):
    """Current signed holdings in quote currency and positive NAV in base currency."""

    model_config = ConfigDict(extra="forbid", strict=True)

    positions: list[Position] = Field(min_length=1, max_length=30)
    portfolio_value: float = Field(gt=0, allow_inf_nan=False)
    base_currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    period: Literal["1y", "2y", "3y", "5y", "10y", "max"] = "5y"
    confidence: float = Field(default=.975, ge=.90, le=.99, allow_inf_nan=False)
    scenarios: list[StressScenario] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_holdings_and_scenarios(self) -> Self:
        tickers = [position.ticker.upper() for position in self.positions]
        if len(set(tickers)) != len(tickers) or any(position.market_value == 0 for position in self.positions):
            raise ValueError("aggregate duplicate tickers and omit zero positions")
        if len({scenario.name for scenario in self.scenarios}) != len(self.scenarios):
            raise ValueError("scenario names must be unique")
        exact_tickers = {position.ticker for position in self.positions}
        for scenario in self.scenarios:
            if set(scenario.asset_shocks) - exact_tickers:
                raise ValueError("scenario contains an unknown ticker")
            for currency in scenario.fx_shocks:
                if len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
                    raise ValueError("FX shocks require uppercase three-letter currency codes")
                if currency == self.base_currency:
                    raise ValueError("cannot shock the base currency")
        return self


def _reject_constant(value: str):
    raise ValueError(f"nonfinite JSON number: {value}")


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_config(path: Path) -> MonitorRequest:
    raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    # strict=True also applies to the nested Position and StressScenario models.
    return MonitorRequest.model_validate(raw, strict=True)


def run(request: MonitorRequest) -> dict:
    result = portfolio.report(
        positions=request.positions, portfolio_value=request.portfolio_value,
        base_currency=request.base_currency, period=request.period,
        confidence=request.confidence, scenarios=request.scenarios,
    )
    decision = result["model_status"]
    return {
        "status": "coverage_alarm" if decision["reason"] == "coverage_alarm" else "ok",
        "record_id": result["audit"]["record_id"], "as_of": decision["as_of"],
        "champion": decision["champion"], "reason": decision["reason"],
        "fallback": decision["fallback"], "warnings": result["warnings"],
        "calibration": decision["calibration"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON portfolio request; holdings are user supplied")
    args = parser.parse_args(argv)
    try:
        summary = run(load_config(args.config))
        encoded = json.dumps(summary, allow_nan=False)
    except Exception as error:
        print(json.dumps({"status": "error", "error_type": type(error).__name__, "error": str(error)}), file=sys.stderr)
        return 2
    print(encoded)
    return 3 if summary["status"] == "coverage_alarm" else 0


if __name__ == "__main__":
    raise SystemExit(main())
