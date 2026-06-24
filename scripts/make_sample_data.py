"""테스트용 샘플 데이터 생성 스크립트.

실제 OCR(GPT 비전) 테스트가 가능하도록, 텍스트가 그려진 서류 이미지
(사업자등록증.png / 신분증.jpg)와 검증대상.xlsx, 그리고 이를 묶은
ZIP 파일을 ``sample_data/`` 아래에 생성한다.

검증 대상 열: 사업자번호, 상호명, 계약일자

케이스 구성:
- 156: 전부 일치 (엑셀과 형식만 다르고 값은 같음)  -> OK(초록)
- 157: 상호명/계약일자 불일치                       -> 불일치(빨강)
- 158: 서류 폴더 없음                               -> 확인필요(노랑)
- (빈 행): 파일폴더/파일번호 비어있음               -> 제외(회색)

실행: python scripts/make_sample_data.py
필요 패키지: Pillow
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

# 출력 위치
ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sample_data"
GROUP_NAME = "이마트24_2"  # 파일폴더 값

FONT_PATH = r"C:\Windows\Fonts\malgun.ttf"
FONT_BOLD_PATH = r"C:\Windows\Fonts\malgunbd.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold and Path(FONT_BOLD_PATH).exists() else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def make_business_license(
    path: Path, company: str, biz_no: str, owner: str, open_date: str
) -> None:
    """사업자등록증 모양의 이미지를 그린다."""
    W, H = 900, 620
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    d.rectangle([20, 20, W - 20, H - 20], outline="#333333", width=3)
    d.text((W // 2, 70), "사 업 자 등 록 증", font=_font(40, bold=True),
           fill="#111111", anchor="mm")
    d.text((W // 2, 120), "( 일반과세자 )", font=_font(22), fill="#555555", anchor="mm")
    d.line([60, 160, W - 60, 160], fill="#999999", width=2)

    rows = [
        ("등록번호", biz_no),
        ("상    호", company),
        ("대 표 자", owner),
        ("개업연월일", open_date),
        ("사업장소재지", "서울특별시 강남구 테헤란로 123"),
        ("업    태", "도소매 / 서비스"),
    ]
    y = 210
    for label, value in rows:
        d.text((90, y), label, font=_font(26, bold=True), fill="#222222")
        d.text((330, y), ":", font=_font(26), fill="#222222")
        d.text((360, y), value, font=_font(26), fill="#000000")
        y += 62

    d.text((W // 2, H - 55), "위와 같이 사업자등록증을 교부합니다.",
           font=_font(20), fill="#444444", anchor="mm")
    img.save(path, quality=92)


def make_id_card(path: Path, name: str, rrn: str) -> None:
    """신분증(주민등록증) 모양의 이미지를 그린다."""
    W, H = 820, 520
    img = Image.new("RGB", (W, H), "#eef3f8")
    d = ImageDraw.Draw(img)

    d.rectangle([15, 15, W - 15, H - 15], outline="#2c5f8a", width=3)
    d.text((60, 50), "주 민 등 록 증", font=_font(38, bold=True), fill="#1b3a57")

    # 사진 영역
    d.rectangle([W - 240, 70, W - 70, 300], outline="#888888", width=2, fill="#dfe6ee")
    d.text((W - 155, 185), "사진", font=_font(24), fill="#888888", anchor="mm")

    d.text((70, 150), name, font=_font(40, bold=True), fill="#111111")
    d.text((70, 220), rrn, font=_font(30), fill="#222222")
    d.text((70, 300), "서울특별시 강남구 테헤란로 123", font=_font(22), fill="#333333")
    d.text((70, H - 80), "2018. 01. 01.", font=_font(24), fill="#333333")
    d.text((70, H - 45), "서울특별시 강남구청장", font=_font(24, bold=True), fill="#1b3a57")
    img.save(path, quality=92)


def build_excel(path: Path) -> None:
    """검증대상.xlsx 작성. 헤더를 2행에 두어 헤더 자동탐색도 확인."""
    wb = Workbook()
    ws = wb.active
    ws.title = "검증대상"
    ws.append(["■ 거래처 서류 검증 대상 목록"])  # 1행: 제목 (헤더 아님)
    ws.append(["파일폴더", "파일번호", "사업자번호", "상호명", "계약일자"])  # 2행: 헤더
    # 엑셀의 '기준값' (서류 이미지 값과 비교 대상)
    ws.append([GROUP_NAME, "156", "123-45-67890", "주식회사 행복마트", "2021.10.08"])
    ws.append([GROUP_NAME, "157", "211-86-12345", "(주)스마일유통", "2022-03-15"])
    ws.append([GROUP_NAME, "158", "777-88-99000", "없는폴더상점", "2023-07-01"])
    ws.append(["", "", "555-55-55555", "폴더정보없음상점", "2020-01-01"])  # 제외 케이스

    for col, width in zip("ABCDE", (14, 10, 16, 22, 14)):
        ws.column_dimensions[col].width = width
    wb.save(path)


def main() -> None:
    if SAMPLE_DIR.exists():
        shutil.rmtree(SAMPLE_DIR)
    SAMPLE_DIR.mkdir(parents=True)

    # 1) 엑셀
    xlsx_path = SAMPLE_DIR / "검증대상.xlsx"
    build_excel(xlsx_path)

    group_dir = SAMPLE_DIR / GROUP_NAME

    # 2) 156번 폴더: 전부 일치 (엑셀과 형식만 다름)
    folder156 = group_dir / "156"
    folder156.mkdir(parents=True)
    make_business_license(
        folder156 / "사업자등록증.png",
        company="행복마트",  # 엑셀 '주식회사 행복마트' 와 정규화 후 일치
        biz_no="1234567890",  # 엑셀 '123-45-67890' 와 정규화 후 일치
        owner="김행복",
        open_date="2021년 10월 08일",  # 엑셀 '2021.10.08' 와 정규화 후 일치
    )
    make_id_card(folder156 / "신분증.jpg", name="김행복", rrn="800101-1******")

    # 3) 157번 폴더: 상호명/계약일자 불일치
    folder157 = group_dir / "157"
    folder157.mkdir(parents=True)
    make_business_license(
        folder157 / "사업자등록증.png",
        company="주식회사 다른상호유통",  # 엑셀 '(주)스마일유통' 과 불일치
        biz_no="211-86-12345",  # 사업자번호는 일치
        owner="이영희",
        open_date="2019.11.20",  # 엑셀 '2022-03-15' 와 불일치
    )
    make_id_card(folder157 / "신분증.jpg", name="이영희", rrn="900202-2******")

    # 4) 158번 폴더: 일부러 만들지 않음 -> 확인필요

    # 5) ZIP 묶기 (웹 UI 업로드용)
    zip_path = SAMPLE_DIR / f"{GROUP_NAME}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(xlsx_path, "검증대상.xlsx")
        for p in group_dir.rglob("*"):
            zf.write(p, p.relative_to(SAMPLE_DIR))

    print("샘플 데이터 생성 완료:")
    print(f"  - 엑셀  : {xlsx_path}")
    print(f"  - 폴더  : {group_dir} (156, 157)")
    print(f"  - ZIP   : {zip_path}")
    print("\n검증할 열 이름 입력 예시: 사업자번호, 상호명, 계약일자")
    print("기대 결과: 156=전부 일치 / 157=상호명·계약일자 불일치 / 158=확인필요 / 빈행=제외")


if __name__ == "__main__":
    main()
