# KOSPI 모닝 전망 (Hermes Agent 스킬)

평일 08:00(KST)에 실행되는 KOSPI 장전 브리핑 스킬입니다. KOSDAQ은 다루지 않습니다.

리포트에 들어가는 내용:
- 전일 투자자별 수급: 현물, 선물(미결제약정 포함), 프로그램, 옵션(기사 기반)
- 간밤 미국 시장
- 강세·약세 테마와 업종별 수급
- 외국인·기관 순매수/순매도 상위 10종목과 매매 이유
- 통계 모델로 낸 오늘 KOSPI 상승 확률과 68% 예상 범위
- 다음 날 자동 채점

## 요구사항
- Hermes Agent 설치. 메신저(Telegram 등)로 받으려면 gateway가 실행 중이어야 함
- python3 (표준 라이브러리만 사용, 추가 패키지 없음)
- 네이버·다음 금융 페이지에 접속 가능한 네트워크

## 설치
```bash
git clone https://github.com/ajitta/kospi-morning-forecast.git && cd kospi-morning-forecast
bash scripts/install.sh                  # 스킬과 스크립트 설치 + 동작 확인
bash scripts/install.sh --cron telegram  # 예약 작업까지 생성 (전달 대상: origin|local|telegram|...)
```
- 업데이트: `git pull && bash scripts/install.sh` (예약 작업은 그대로 유지되고 스크립트만 교체됨)
- 예약 시각은 Hermes에 설정된 시간대를 따릅니다. `config.yaml`에 `timezone: Asia/Seoul`로 두는 것을 권장합니다. UTC 서버라면 설치 스크립트가 자동으로 23:00(일~목)으로 바꿔 등록합니다.
- 바로 한 번 실행해 보려면: `hermes cron list`로 job id를 확인한 뒤 `hermes cron run <id>`
- 데이터만 확인하려면: `python3 ~/.hermes/scripts/kospi_morning.py --force`

## 구성
- `SKILL.md`: 리포트 작성 규칙, 예측 규칙, 데이터 출처, 주의사항
- `references/cron-prompt.md`: 예약 작업 프롬프트
- `scripts/kospi_morning.py`: 데이터 수집. 휴장일에는 `{"wakeAgent": false}`를 출력해 실행을 건너뜀
- `scripts/kospi_model.py`: 통계 기준 모델
  - 입력: 간밤 SOX·나스닥100 수익률을 각 지수의 20일 변동성으로 나눈 값
  - 상승 확률: 로지스틱 회귀
  - 68% 예상 범위: 과거 예측 오차 분포 × 현재 KOSPI 변동성
  - 2016년 이후 데이터로 매 실행 때마다 다시 학습하고, 2019년 이후 구간으로 성능을 검증함
- `scripts/kospi_record.py`: 예측 기록. AI의 조정은 ±10%p까지만 허용하고 조정 사유가 필수
- 예측 기록 파일: `~/.hermes/data/kospi_forecast/predictions.jsonl`

## 유의사항
- **네이버·다음 비공식 API:** 데이터는 네이버·다음 금융의 공개 웹 API에서 가져옵니다. 공식 API가 아니어서 사이트가 바뀌면 깨질 수 있습니다. 상업적으로 쓰기 전에는 각 사의 약관을 확인하세요.
- **모델 성능:**
  - 2019~2026년 학습에 쓰지 않은 구간으로 시험한 결과, 방향 적중률은 64%였습니다. 매일 "상승"으로만 찍으면 55.5%입니다.
  - 68% 범위 안에 실제로 들어온 비율은 65.5%였습니다.
  - 급락·급등 같은 꼬리 사건은 이 모델로 잡지 못합니다.
- **휴장일 목록:** `kospi_morning.py`의 `KRX_HOLIDAYS`는 2027년까지만 들어 있습니다. 매년, 그리고 선거·임시공휴일이 생길 때마다 갱신해야 합니다.
- **면책:** 투자 판단 참고용이며 투자 권유가 아닙니다.

## 라이선스
MIT
