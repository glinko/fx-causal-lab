# Dynamic Economic Neural Network (DENN)

Статус: design specification / research architecture  
Фокус: EUR/USD как один из выходов динамической модели экономического состояния  
Горизонты: 1d, 5d, 20d, 60d  
Принцип: point-in-time, без look-ahead и revision leakage

## 1. Модель мира

DENN рассматривает экономику как динамический граф состояний. Узел `i` имеет наблюдаемое или скрытое состояние `x_i(t)`, память `m_i(t)` и скрытое представление `h_i(t)`. Ребро `i → j` имеет медленно меняющуюся структурную силу и контекстную динамическую поправку.

- **Node state:** что известно в момент `t`: oil, yields, VIX, CPI, COT и другие величины.
- **Structural edge weight:** типичная сила связи.
- **Dynamic edge delta:** изменение важности связи в текущем режиме.
- **Temporal memory:** остаточная важность старых состояний и событий.
- **Horizon/frequency gate:** масштаб времени, на котором связь допустима.

Простейшая память:

```text
m_i(t) = Σ_k α_i(k) φ_i(x_i(t-k))
α_i(k) = exp(-ln(2) × age_k / H_i)
```

Предпочтителен half-life на уровне ребра и горизонта `H_ij(h)`. Эффективный вес:

```text
w_ij(t,h,f) = M_ij × [w0_ij + Δw_ij(t)] × D_ij(age,h) × G_ij(f,h)
```

`M_ij` — causal mask, `w0_ij` — structural weight, `Δw_ij(t)` — regime/context delta, `D_ij` — decay, `G_ij` — frequency/horizon gate.

```text
h_j(t) = Update(h_j(t-1), x_j(t), Σ_i w_ij(t) Message(h_i(t), e_ij))
```

Новый временной слой — snapshot/event состояния мира, проходящий через одну и ту же обучаемую структуру. Он не добавляет новые параметры каждую минуту.

## 2. Типы узлов

- **Continuous market:** EUR/USD, Brent, WTI, Gold, VIX, equity indices, Treasury и euro-area yields, credit spreads, cross-currency basis.
- **Macro state:** CPI/PCE, unemployment, payrolls, GDP, PMI, wages, trade balance, fiscal variables.
- **Events:** FOMC, ECB, CPI, NFP, OPEC, Treasury auctions, index rebalances, option expiry, major corporate filings.
- **Institutional flows:** CFTC categories, CTA exposure, rebalancing pressure, hedge ratios, dealer gamma при наличии данных.
- **Latent:** risk aversion, inflation anxiety, growth expectations, USD funding stress, policy stance. Они имеют confidence и provenance и не считаются наблюдаемыми фактами.
- **Targets:** EUR/USD return buckets, quantiles, direction, volatility и MAE/MFE для 1d/5d/20d/60d.

## 3. Point-in-time schema

Каждое значение хранит минимум:

```text
event_time
published_at
available_at
ingested_at
value
revision_id
source
quality
```

Revised value нельзя передавать модели до его реальной публикации. Current-history downloads без подтверждённых vintages полезны для exploratory/spectral baseline, но получают `strict_pit_eligible=false`.

Базовые источники: U.S. Treasury, ECB, FRED/ALFRED, EIA, CFTC, BLS, BEA, Census, Eurostat, Fed/ECB и рыночный provider EUR/USD. Consensus — optional provider и не блокирует MVP.

## 4. Частоты и события

Для continuous series сохраняются raw level, return/difference, rolling volatility, z-score/percentile, FFT bands, wavelet bands, spectral coherence и phase lag относительно target/mediators.

Sparse macro release нельзя превращать в плавную дневную кривую. Его представление: event impulse, surprise при наличии легитимного consensus, `age_since_release` и `last_known_value`.

Decay разделён на feature age, event effect и structural persistence. Начальный baseline:

```text
D(age) = exp(-ln(2) × age / H)
```

Priors: market microstate — часы/дни; rates/risk — дни/недели; COT — недели; macro — недели/месяцы; fiscal/technology regime — месяцы. Half-life позже оценивается только walk-forward.

## 5. Веса и архитектурные этапы

1. **Statistical priors:** correlation, rank correlation, cross-correlation, local projections, Granger, VAR/SVAR, spectral/wavelet coherence, event studies.
2. **ML baselines:** Linear/ElasticNet, LightGBM/XGBoost, TFT. Importance не называется причинностью.
3. **Graph model:** causal mask и `w0` инициализируются priors, затем обучаются; regime encoder формирует `Δw(t)`.

Рекомендуемая параметризация:

```text
w_eff = mask × tanh(w0 + delta_net(context)) × decay × freq_gate
```

Регуляризация: sparsity, temporal smoothness, soft sign prior и stability across folds.

Архитектура v0.1: feature store → type-specific node encoders → GRU/TCN/Neural CDE/TGN-style memory → masked Graph Attention/Transformer → regime encoder → multi-horizon heads → calibration. TGN, EvolveGCN, TFT и Neural CDE — design references и baseline candidates, а не заранее выбранные победители.

## 6. World model и выходы

Предпочтительно multi-task обучение: будущие states rates/VIX/oil/relative equities, распределение EUR/USD и auxiliary event outcomes.

```text
L = λ_fx L_fx + Σ_k λ_k L_aux,k + λ_cal L_cal + λ_sparse L_sparse + λ_smooth L_smooth
```

Для EUR/USD нужны quantile/categorical/probabilistic losses. Для каждого горизонта модель выдаёт вероятности return buckets `<-1.5%`, `-1.5…-0.5%`, `-0.5…+0.5%`, `+0.5…+1.5%`, `>+1.5%`, а также expected return, quantiles, uncertainty, calibration score, active paths и edge contributions.

AnyJev остаётся отдельной экспериментальной readout-веткой поверх structured world state. Сравнение: logistic, LightGBM, TFT и graph head. Случайный k-fold запрещён.

## 7. Обучение, inference и explainability

Только chronological/purged walk-forward. Для overlapping 20d/60d targets обязателен embargo/gap. Метрики: log loss, Brier, ECE, CRPS/quantile loss, calibration curves, regime stability, information coefficient; direction accuracy вторична. Экономическая полезность после costs оценивается позже.

- **Fast state update:** новый datapoint/event обновляет node state, memory и `Δw(t)`.
- **Slow parameter update:** `w0`, encoders и kernels переобучаются weekly/monthly после walk-forward checks.

Каждый прогноз сохраняет top nodes/edges, current vs base weight, decay, horizon/frequency gate, ablation delta и uncertainty. Attention и edge weight сами по себе не являются доказательством причинности.

## 8. Этапы DENN

- **MVP-0:** data + static baselines. EUR/USD, US–EA 2Y/10Y spreads, Brent, VIX, relative equities, CFTC; D1 с 2004+ где возможно.
- **MVP-1:** LightGBM/TFT на point-in-time features; multi-horizon probabilities.
- **MVP-2:** NetworkX/YAML topology + PyTorch Geometric/DGL; learned base weights and decay.
- **MVP-3:** wavelet bands, coherence/phase и learned frequency gates.
- **MVP-4:** regime encoder → `Δw(t)`, base/delta decomposition и edge stability report.
- **MVP-5:** AnyJev experiment с calibrated typed heads.

Dynamic GNN не является первым шагом. Сначала реализуется `denn/` с единой snapshot schema, decay kernels и deterministic feature generation, затем baseline на 5–6 continuous factors и воспроизводимый walk-forward pipeline.

## 9. Критерий успеха и риски

Проект успешен даже без устойчивого прогноза EUR/USD, если он воспроизводим, не содержит leakage, показывает устойчивость связей по горизонтам, калибрует uncertainty, умеет отвергать гипотезы и честно сравнивается с простыми baselines.

Главные риски: non-stationarity, false causality, revisions, mixed frequencies, correlated features/double counting, attention-as-causality, малая выборка macro events, calibration shift и overfitting dynamic weights.

## 10. Порядок текущей реализации

1. Free/public data coverage и реальные локальные Parquet.
2. Общая D1 сетка без скрытого forward fill.
3. Deterministic DENN snapshot/decay features и purged walk-forward baseline на continuous factors — реализовано в v0.14.
4. FFT, Haar wavelet energy, Welch coherence/phase и lag baseline — реализовано в v0.15 как full-sample descriptive diagnostic.
5. Rolling/expanding spectral stability без выбора параметров по полному sample.
6. Только затем temporal/graph training.

Consensus schema сохраняется, но vendor evaluation приостановлен до завершения free/public dataset и spectral MVP. Существующий CPI/NFP tick/M1 код сохраняется как первый event-study prototype.

## 11. Research references

- Rossi et al., *Temporal Graph Networks for Deep Learning on Dynamic Graphs* (2020): https://arxiv.org/abs/2006.10637
- Pareja et al., *EvolveGCN* (2019): https://arxiv.org/abs/1902.10191
- Lim et al., *Temporal Fusion Transformers* (2019): https://arxiv.org/abs/1912.09363
- Kidger et al., *Neural Controlled Differential Equations* (2020): https://arxiv.org/abs/2005.08926
- Nokia Applied Research, AnyJev (2026): https://github.com/nokia-applied-research/AnyJev
