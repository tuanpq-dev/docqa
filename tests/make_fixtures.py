"""Tạo tài liệu mẫu trong tests/fixtures/ (chạy một lần; cần reportlab + font DejaVuSans).

    pip install reportlab==5.1.0
    python tests/make_fixtures.py
"""
from pathlib import Path

import docx
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "fixtures"
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

VI_PAGES = [
    """Hướng dẫn trồng và chăm sóc cà phê vối ở Tây Nguyên

Cà phê vối (Robusta) là cây công nghiệp chủ lực của các tỉnh Đắk Lắk, Gia Lai, Đắk Nông, Kon Tum và Lâm Đồng. Cây ưa khí hậu nóng ẩm, nhiệt độ thích hợp từ 22 đến 26 độ C, lượng mưa hằng năm khoảng 1.800 đến 2.500 mm.

Đất trồng phù hợp nhất là đất đỏ bazan, tầng canh tác dày trên 70 cm, độ pH từ 4,5 đến 5,5. Trước khi trồng cần dọn sạch gốc rễ cây cũ, cày sâu 30 đến 40 cm và để ải ít nhất một tháng.

Mật độ trồng khuyến nghị là 1.100 cây trên một héc-ta, khoảng cách 3 m x 3 m. Hố trồng có kích thước 60 x 60 x 60 cm, bón lót 10 đến 15 kg phân chuồng hoai mục cùng 0,5 kg lân nung chảy cho mỗi hố.""",
    """Thời vụ và kỹ thuật tưới nước

Thời vụ trồng tốt nhất là đầu mùa mưa, từ tháng 5 đến tháng 7. Trồng sớm giúp cây có đủ thời gian phát triển bộ rễ trước khi mùa khô đến.

Tưới nước là khâu quan trọng nhất trong mùa khô. Đợt tưới đầu tiên thực hiện khi nụ hoa đã phát triển đầy đủ, thường vào tháng 1 hoặc tháng 2. Mỗi gốc cần từ 500 đến 600 lít nước cho một lần tưới, chu kỳ tưới cách nhau 20 đến 25 ngày.

Tưới nhỏ giọt giúp tiết kiệm khoảng 30 đến 40 phần trăm lượng nước so với tưới gốc truyền thống, đồng thời có thể kết hợp bón phân qua hệ thống tưới.""",
    """Bón phân, phòng trừ sâu bệnh và thu hoạch

Cà phê kinh doanh cần bón khoảng 250 kg đạm (N), 100 kg lân (P2O5) và 250 kg kali (K2O) cho một héc-ta mỗi năm, chia làm bốn lần bón trong mùa mưa.

Các loại sâu bệnh hại chính gồm rệp sáp, mọt đục quả, bệnh gỉ sắt và bệnh nấm hồng. Biện pháp phòng trừ ưu tiên là quản lý dịch hại tổng hợp (IPM): tỉa cành tạo độ thông thoáng, vệ sinh vườn, chỉ dùng thuốc khi mật độ sâu bệnh vượt ngưỡng.

Thu hoạch khi tỷ lệ quả chín đạt trên 95 phần trăm, thường từ tháng 10 đến tháng 12. Năng suất trung bình đạt 3 đến 4 tấn nhân trên một héc-ta. Quả sau khi hái cần phơi hoặc sấy đến độ ẩm 12,5 phần trăm để bảo quản.""",
]

EN_PAGES = [
    """Home Solar Panel Maintenance Guide

Solar panels are durable, but regular maintenance keeps them working at peak efficiency. A typical residential system of 5 kW produces between 6,000 and 7,500 kWh per year depending on location and orientation.

Dust, pollen and bird droppings can reduce output by 5 to 25 percent. In dry and dusty regions, clean the panels every three months; in rainy climates, twice a year is usually enough.""",
    """Cleaning procedure and safety

Always switch off the system at the inverter before cleaning. Use a soft brush or sponge with lukewarm water and a small amount of mild soap. Never use high-pressure washers, because they can damage the seals and the anti-reflective coating.

Clean early in the morning or late in the evening, when the panels are cool. Spraying cold water on hot glass may cause thermal shock and micro-cracks. If the panels are on a steep roof, hire a professional who uses fall protection.""",
    """Inspection, inverter and warranty

Inspect the panels visually every six months for cracks, discoloration, loose wiring and corrosion on the mounting rails. Check the inverter display monthly: a green light normally means the system is operating correctly, while a red or orange light indicates a fault.

String inverters usually last 10 to 15 years and are the component most likely to need replacement. Most panel manufacturers offer a 25-year performance warranty that guarantees at least 80 percent of the original output.""",
]


def font():
    for p in FONT_PATHS:
        if Path(p).is_file():
            pdfmetrics.registerFont(TTFont("Uni", p))
            return "Uni"
    raise SystemExit("Không tìm thấy font Unicode")


def write_pdf(path: Path, pages: list[str], fname: str):
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    for page in pages:
        y = h - 60
        for para in page.split("\n\n"):
            words, line = para.split(), ""
            for word in words:
                trial = (line + " " + word).strip()
                if pdfmetrics.stringWidth(trial, fname, 11) > w - 120:
                    c.setFont(fname, 11)
                    c.drawString(60, y, line)
                    y -= 16
                    line = word
                else:
                    line = trial
            c.setFont(fname, 11)
            c.drawString(60, y, line)
            y -= 28
        c.showPage()
    c.save()


def write_scanned(path: Path):
    c = canvas.Canvas(str(path), pagesize=A4)
    for _ in range(2):
        for i in range(40):
            c.rect(60, 100 + i * 15, 400 - (i * 7) % 120, 8, fill=1)
        c.showPage()
    c.save()


def write_docx(path: Path, pages: list[str]):
    d = docx.Document()
    for page in pages:
        title, *paras = page.split("\n\n")
        d.add_heading(title, level=1)
        for p in paras:
            d.add_paragraph(p)
    d.save(str(path))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    f = font()
    write_pdf(OUT / "vi_sample.pdf", VI_PAGES, f)
    write_pdf(OUT / "en_sample.pdf", EN_PAGES, f)
    write_scanned(OUT / "scanned.pdf")
    write_docx(OUT / "vi_sample.docx", VI_PAGES)
    (OUT / "corrupt.pdf").write_bytes(b"%PDF-1.4\n%garbage that is not a real pdf\n" + bytes(range(256)) * 4)
    (OUT / "notes_en.md").write_text("\n\n".join(EN_PAGES), encoding="utf-8")
    print("Đã tạo fixtures trong", OUT)
