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
bash scripts/install.sh --cron telegram  # 예약 작업까지 생성: 08:00 브리핑 + 장 마감 후 스냅샷 (전달 대상: origin|local|telegram|...)
bash scripts/install.sh --with-history   # (선택) 운영 중인 예측 기록을 받아 성적표에 채움
```
- 업데이트: `git pull && bash scripts/install.sh` (예약 작업은 그대로 유지되고 스크립트만 교체됨)
  - v1.1 이하에서 올리는 경우 한 번만: `bash scripts/install.sh --snapshot-cron telegram` (스냅샷 작업 추가)
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
- `scripts/kospi_snapshot.py`: 평일 16:10·20:10에 LLM 없이 도는 스크립트 작업
  - 프로그램 매매, 거래대금·등락종목, 업종별 수급, 테마 등락은 포털이 다음 날 장 시작 전에 초기화해서 08:00에는 다시 받을 수 없음
  - 그래서 장 마감 후 `snapshots/<날짜>.json`에 저장해 두고 다음 날 아침 브리핑이 읽음. 정상이면 아무 메시지도 보내지 않음
- 예측 기록: `~/.hermes/data/kospi_forecast/`
  - `predictions.jsonl`: 장전 예측
  - `actuals.jsonl`: 다음 날 수집 스크립트가 채우는 실제 종가·등락률. 두 파일 모두 추가만 함
  - 성적표는 이 두 파일로 계산하므로 기간 제한 없이 누적됨

## 예측 기록 (트랙레코드)
- 실제 운영 기록은 이 저장소의 [`data` 브랜치](https://github.com/ajitta/kospi-morning-forecast/tree/data)에 거래일마다 자동으로 커밋됩니다.
- 모델 학습 데이터는 저장하지 않습니다. 실행할 때마다 2016년 이후 일봉을 네이버에서 새로 받아 학습하므로, 새로 설치해도 모델과 백테스트는 똑같이 재현됩니다. 처음 설치하면 비어 있는 것은 예측 성적표뿐입니다.
- `--with-history`를 쓰면 데이터 디렉터리가 비어 있을 때만 `data` 브랜치를 받아 채웁니다.
  - 받은 뒤에 쌓이는 본인 예측은 로컬에만 커밋되고 push되지 않습니다.
  - 성적표에는 받은 기록과 본인 기록이 함께 집계됩니다.
  - 주 용도는 같은 운영자가 서버를 옮기거나 다시 설치할 때 복구하는 것입니다.
- 직접 기록을 공개하려면 데이터 디렉터리를 본인 저장소에 연결하고 `git -C ~/.hermes/data/kospi_forecast config kospi.push true`를 설정하세요. 예측을 기록할 때마다 자동으로 push됩니다.

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
