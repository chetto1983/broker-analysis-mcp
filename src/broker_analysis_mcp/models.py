"""Input models for the parametric tools; the MCP server validates requests against them."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import SpecError

AnalysisPeriod = Literal["1y", "2y", "3y", "5y", "10y", "max"]
HistoryPeriod = Literal["1d", "5d", "1mo", "3mo", "6mo", "ytd", "1y", "2y", "3y", "5y", "10y", "max"]
Finite = Annotated[float, Field(allow_inf_nan=False)]
AnnualRate = Annotated[float, Field(gt=-1, allow_inf_nan=False)]
Confidence = Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
PortfolioConfidence = Annotated[float, Field(ge=.90, le=.99, allow_inf_nan=False)]
PositiveAmount = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Lookback = Annotated[int, Field(ge=2, le=1000)]
WilderPeriod = Annotated[int, Field(ge=2, le=200)]
MACDSlow = Annotated[int, Field(ge=3, le=400)]
MACDSignal = Annotated[int, Field(ge=2, le=100)]
BandWidth = Annotated[float, Field(ge=.1, le=5, allow_inf_nan=False)]


class RiskParameters(BaseModel):
    period: AnalysisPeriod = "3y"
    risk_free_annual: AnnualRate = 0.0
    confidence: Confidence = .95


class HistoryParameters(BaseModel):
    period: HistoryPeriod = "1y"


class SnapshotParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: Annotated[list[Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9^=._-]+$")]], Field(min_length=1, max_length=30)]
    period: HistoryPeriod = "3y"
    benchmark: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9^=._-]+$")] | None = None
    base_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    include_incomplete_session: bool = False

    @model_validator(mode="after")
    def unique_tickers(self):
        self.tickers = [ticker.upper() for ticker in self.tickers]
        if len(set(self.tickers)) != len(self.tickers):
            raise ValueError("snapshot tickers must be unique")
        if self.benchmark:
            self.benchmark = self.benchmark.upper()
        return self


class CapitalParameters(BaseModel):
    risk_free_annual: AnnualRate | None = None
    equity_risk_premium: Finite | None = None
    beta: Finite | None = None


class TechnicalParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sma_fast: Lookback = 50
    sma_slow: Lookback = 200
    rsi_period: WilderPeriod = 14
    macd_fast: WilderPeriod = 12
    macd_slow: MACDSlow = 26
    macd_signal: MACDSignal = 9
    bollinger_n: Lookback = 20
    bollinger_k: BandWidth = 2.0
    atr_period: WilderPeriod = 14

    @model_validator(mode="after")
    def ordered_macd(self):
        from .catalog import resolve_params

        resolve_params("macd", {"fast": self.macd_fast, "slow": self.macd_slow, "signal": self.macd_signal})
        return self


def validate_parameters(model: type[BaseModel], **values) -> BaseModel:
    try:
        return model(**values)
    except ValidationError as err:
        raise SpecError(f"invalid input: {err}") from err

SeriesId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$", description="lowercase id, e.g. ema_fast")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class IndicatorSpec(BaseModel):
    """One catalog indicator with its parameters, e.g. {"id": "rsi14", "fn": "rsi", "params": {"n": 14}}."""

    id: SeriesId
    fn: str = Field(description="catalog name: see list_indicator_catalog")
    params: dict[str, float | int | str] = Field(default_factory=dict)
    pane: Literal["auto", "price", "lower"] = "auto"
    color: Color | None = None


class SeriesSpec(BaseModel):
    """A series derived by expression, e.g. {"id": "gap", "expr": "close / sma200 - 1"}."""

    id: SeriesId
    expr: str
    pane: Literal["price", "lower"] = "lower"
    color: Color | None = None


class ConditionSpec(BaseModel):
    """A condition evaluated on every bar, e.g. {"name": "oversold in uptrend", "when": "rsi14 < 30 and close > sma200"}.

    mode "edge" fires on the bar the condition becomes true; "state" counts every true bar."""

    name: Annotated[str, Field(min_length=1, max_length=60)]
    when: str
    mode: Literal["edge", "state"] = "edge"
    marker: Literal["up", "down"] = "up"


class LevelSpec(BaseModel):
    """A horizontal level drawn on the price chart."""

    price: Finite
    label: Annotated[str, Field(max_length=40)] = ""


class HookAction(BaseModel):
    """Where a fired hook is delivered: an HTTP POST of JSON, or a line appended to a JSONL file."""

    type: Literal["webhook", "file"]
    target: Annotated[str, Field(max_length=500, description="URL for webhook, file name for file")]


class Position(BaseModel):
    """Signed market value in the security's quote currency (GBP, not GBp)."""

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9^=._-]+$")
    market_value: float = Field(allow_inf_nan=False)


class StressScenario(BaseModel):
    """Hypothetical local-price and FX shocks, in decimals. Missing shocks mean zero."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    asset_shocks: dict[str, Annotated[float, Field(ge=-1, le=10, allow_inf_nan=False)]] = Field(default_factory=dict)
    fx_shocks: dict[str, Annotated[float, Field(ge=-1, le=10, allow_inf_nan=False)]] = Field(default_factory=dict)
