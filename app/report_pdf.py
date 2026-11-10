"""기여 리포트 PDF (fpdf2 + 나눔고딕). 합계·비율·순위는 넣지 않는다."""

from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from app import clock

FONT_DIR = Path(__file__).parent / "fonts"

TYPE_LABEL = {"OUTPUT": "산출물", "MEETING": "회의 참여", "COORDINATION": "조율·소통", "REVIEW": "검토·피드백"}
CRIMSON = (153, 0, 51)
GRAY = (90, 90, 90)


def _d(iso: str | None) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone(clock.KST).strftime("%Y.%m.%d")


class _PDF(FPDF):
    serial = ""

    def footer(self):
        self.set_y(-12)
        self.set_font("Nanum", size=8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, f"팀플로그 기여 리포트 · {self.serial} · {self.page_no()}쪽", align="C")


def render(content: dict, views: list[dict], serial: str, submitted_at: str | None) -> bytes:
    pdf = _PDF(format="A4")
    pdf.serial = serial
    pdf.add_font("Nanum", "", str(FONT_DIR / "NanumGothic-Regular.ttf"))
    pdf.add_font("Nanum", "B", str(FONT_DIR / "NanumGothic-Bold.ttf"))
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    w = pdf.epw

    def text(s: str, size=10, bold=False, color=(0, 0, 0), h=5.5, after=1.5):
        pdf.set_font("Nanum", "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(w, h, s, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(after)

    # 표지 정보
    text("팀플로그 기여 리포트", size=18, bold=True, color=CRIMSON, h=9, after=2)
    team = content["team"]
    text(f"{team['course_name']} · {team['project_name']}", size=12, bold=True)
    text(
        f"기간 {_d(content['period']['from'])} ~ {_d(content['period']['to'])}   ·   "
        f"생성 {_d(content['generated_at'])}   ·   제출 {_d(submitted_at) if submitted_at else '미제출'}   ·   번호 {serial}",
        size=9,
        color=GRAY,
        after=4,
    )

    # 안내
    pdf.set_fill_color(249, 236, 241)
    pdf.set_font("Nanum", "", 9.5)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(w, 5.5, content["notice"], fill=True, padding=3, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # 역할 약속
    ag = content.get("agreement")
    text("확정된 역할 약속", size=12, bold=True, color=CRIMSON)
    if ag:
        for r in ag["roles"]:
            text(f"· {r['name']}: {r['role']}", size=10, after=0.5)
        rules = [f"연락은 {ag['reply_hours']}시간 안에 답장"] + ([ag["meeting_rule"]] if ag.get("meeting_rule") else []) + ag["rules"]
        text("팀 규칙: " + " / ".join(rules), size=9.5, color=GRAY, after=4)
    else:
        text("확정된 역할 약속이 없습니다.", size=10, color=GRAY, after=4)

    # 팀원별 기록
    for m in content["members"]:
        pdf.set_draw_color(*CRIMSON)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
        pdf.ln(2)
        text(m["name"] + (" (팀을 나감)" if m["left"] else ""), size=13, bold=True, after=1)
        for label, key in (("확인된 기록", "confirmed"), ("확인되지 않은 기록", "unconfirmed")):
            groups = m[key]
            if not any(groups.values()):
                continue
            text(label, size=10.5, bold=True, color=CRIMSON if key == "confirmed" else GRAY, after=0.5)
            for t, items in groups.items():
                if not items:
                    continue
                text(f"[{TYPE_LABEL.get(t, t)}]", size=9.5, bold=True, after=0.3)
                for e in items:
                    flags = []
                    if e["late"]:
                        flags.append("사후 기록")
                    if e["no_output"]:
                        flags.append("설명만 있음")
                    if e["status"] == "ON_HOLD":
                        flags.append("보류")
                    head = f"{_d(e['date'])}  {e['task'] or '할 일 없음'}"
                    if flags:
                        head += "  (" + ", ".join(flags) + ")"
                    text("· " + head, size=9.5, after=0)
                    detail = []
                    if e["note"]:
                        detail.append(e["note"])
                    if e["link"]:
                        detail.append(e["link"])
                    if e["file"]:
                        detail.append("첨부 파일 있음")
                    if e["confirmed_by"]:
                        detail.append("확인: " + ", ".join(e["confirmed_by"]))
                    if detail:
                        text("   " + " | ".join(detail), size=8.5, color=GRAY, h=4.5, after=0.8)
            pdf.ln(1)
        if not any(m["confirmed"].values()) and not any(m["unconfirmed"].values()):
            text("이 기간에 남긴 기록이 없습니다.", size=9.5, color=GRAY)
        pdf.ln(2)

    # 열람과 이의 의견
    text("열람 현황과 이의 의견", size=12, bold=True, color=CRIMSON)
    for v in views:
        state = f"열람 {_d(v['viewed_at'])}" if v["viewed_at"] else "미열람"
        line = f"· {v['display_name']}: {state}"
        if v.get("objection"):
            line += f" / 의견: {v['objection']}"
        text(line, size=9.5, after=0.5)
    return bytes(pdf.output())
