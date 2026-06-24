# AI_FESTIVAL_OCR

거래처 제출 서류(사업자등록증·신분증 등)를 OCR(OpenAI 비전 모델)로 읽어,
엑셀에 입력된 기준 데이터와 자동 대조하고 불일치/확인필요 항목을 셀 색으로
표시해 주는 웹 서비스입니다.

> 설계 원칙: **"값 추출은 GPT가, 비교/판정은 코드가"** 한다.

## 주요 기능

- 파일 업로드(엑셀 + 서류 ZIP) + 검증할 열 이름 입력만으로 검증 → 색칠된 결과 엑셀 다운로드
- 한 입력란에서 **검증대상 .xlsx 와 서류 .zip 을 함께(여러 개) 업로드** 가능
- 검증 대상 열은 **하드코딩하지 않고 사용자 입력**으로 받음 (헤더 텍스트와 매칭)
- 헤더 행 위치를 고정하지 않고, 입력한 열 이름이 가장 많이 발견되는 행을 헤더로 판단
- 값의 형태(숫자/날짜/이름)에 따라 자동 정규화 후 비교 → 오탐 방지
- 결과: 셀 색칠(빨강=불일치, 노랑=확인필요, 초록=일치, 회색=제외) + `검증결과` 요약 열

## 입력 구조

검증 화면의 단일 업로드란에 **엑셀(.xlsx)과 서류 ZIP(.zip)을 함께** 올립니다.
엑셀은 ZIP 안에 포함해도 되고, ZIP과 별도로 따로 올려도 됩니다.
(따로 올린 엑셀이 있으면 그 엑셀을 우선 사용)

```
검증대상.xlsx               ← 검증 기준 엑셀 (따로 업로드 또는 ZIP 내부)
이마트24_2.zip
 └─ 이마트24_2/              ← 엑셀의 '파일폴더' 값과 매칭
      ├─ 156/                ← 엑셀의 '파일번호' 값과 매칭
      │    ├─ 사업자등록증.jpg
      │    └─ 신분증.jpg
      └─ 157/ ...
```

각 행의 서류는 `{파일폴더}/{파일번호}/` 폴더 안 이미지(jpg/png 등)입니다.

## 설치 및 실행

```bash
# 1) 가상환경 (선택)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2) 의존성 설치
pip install -r requirements.txt

# 3) 환경변수 설정 (.env 파일 생성, .env.example 참고)
#    OPENAI_API_KEY=sk-...

# 4) 서버 실행 (프로젝트 루트에서)
uvicorn main:app --reload
# 또는
python main.py
```

실행 후 브라우저에서 http://127.0.0.1:8000 접속 → ZIP 업로드 + 검증할 열 입력.

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API 키 (필수) | - |
| `OPENAI_VISION_MODEL` | 사용할 비전 모델 | `gpt-4o` |
| `USE_DUMMY_EXTRACTOR` | `1`이면 GPT 호출 없이 더미 추출 (뼈대 테스트용) | - |

### 더미 모드 테스트

GPT 호출 없이 전체 흐름을 확인하려면 `USE_DUMMY_EXTRACTOR=1` 로 두고,
각 서류 폴더(`{파일폴더}/{파일번호}/`) 안에 `_mock.json` 을 넣으면 그 값이
추출 결과로 사용됩니다.

```json
{ "사업자번호": "123-45-67890", "상호명": "주식회사 테스트", "계약일자": "2021.10.08" }
```

전체 흐름(ZIP 생성 → 검증 → 결과 확인)을 한 번에 점검하는 스모크 테스트:

```bash
# Windows PowerShell
$env:PYTHONPATH = (Get-Location).Path; python scripts/smoke_test.py
# macOS/Linux
PYTHONPATH=. python scripts/smoke_test.py
```

## 코드 구조

router / service / schema 레이어드 구조로 구성했습니다.

```
main.py                            # FastAPI 앱 진입점 (프로젝트 루트)
app/
├─ core/
│  └─ config.py                     # 환경변수/설정 중앙화
├─ routers/
│  └─ verification.py               # 엔드포인트 (GET / , POST /api/verify)
├─ services/
│  ├─ verification_service.py       # verify_zip(): 핵심 검증 로직 (라우터가 이것만 호출)
│  ├─ extraction_service.py         # extract_fields_from_documents(): GPT 값 추출 (모델 교체 용이)
│  ├─ normalization.py              # 값 정규화 + 비교/판정 (코드가 담당)
│  └─ excel_service.py              # 헤더 탐색 + 셀 색칠 유틸
├─ schemas/
│  └─ verification.py               # VerifyResult 등 데이터 스키마
└─ templates/
   └─ index.html                    # 업로드/다운로드 페이지
```

## 검증 판정 기준

| 상태 | 조건 | 색 |
|------|------|----|
| 일치 | 정규화 후 기준값 == 추출값 | 초록 |
| 불일치 | 정규화 후에도 값이 다름 | 빨강 |
| 확인필요 | 서류에서 값을 못 찾음 / 서류 폴더 없음 | 노랑 |
| 제외 | 파일폴더/파일번호가 비어있는 행 | 회색 |
