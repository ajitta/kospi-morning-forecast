# KOSPI 모닝 전망 — 예측 기록 (data 브랜치)

[kospi-morning-forecast](https://github.com/ajitta/kospi-morning-forecast) 스킬이 매 거래일 아침 자동으로 커밋하는 실제 운영 기록입니다. 코드는 `main` 브랜치에 있습니다.

## 파일
- `predictions.jsonl`: 장 시작 전(08:00 KST 무렵)에 기록한 예측입니다. 추가만 하고 수정하지 않습니다. 같은 날짜가 여러 번 있으면 마지막 줄을 씁니다.
  - `p_model`: 통계 기준 P(상승)%
  - `p_final`: AI 조정 후 P(상승)% (기준 대비 ±10%p 이내)
  - `low` / `high`: 모델 68% 범위 (전일 종가 대비 %)
  - `adj_reason`, `thesis`: 조정 사유, 핵심 근거
  - `recorded_at`: 기록 시각 (UTC)
- `actuals.jsonl`: 다음 날 아침 수집 스크립트가 채우는 실제 결과입니다. 추가만 합니다.
  - `close`: KOSPI 종가
  - `chg`: 전일 대비 등락률 %
  - `src`: 출처 (네이버 금융)

채점: `chg > 0`이면 상승입니다. 예측 방향은 P(상승) ≥ 50%면 상승입니다. Brier 점수와 68% 범위 포함률도 계산하며, 기준(`p_model`)과 최종(`p_final`)을 따로 채점합니다.

## 받기
```bash
bash scripts/install.sh --with-history   # 예측 기록이 비어 있는 설치에 이 기록을 채움
```
받은 뒤에 쌓이는 본인 예측은 로컬에만 커밋되고 push되지 않습니다.

투자 판단 참고용이며 투자 권유가 아닙니다.
