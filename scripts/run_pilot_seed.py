"""Pilot seeding helper script for T-402."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.collab_seed import seed_merged_book
from scripts.collab_sweep import GitHubCLIClient, run_sweep
from scripts.evaluate import run_evaluation
from scripts.helper import parse_segments

CH01_TRANSLATIONS = {
    "heading": "Chương I",
    "p1": (
        'Đối với Sherlock Holmes, nàng mãi mãi là "người phụ nữ ấy". Hiếm khi nào tôi nghe anh '
        'nhắc đến nàng bằng một danh xưng nào khác. Trong mắt anh, nàng làm lu mờ và vượt lên '
        'trên toàn bộ giới nữ của mình. Điều đó chẳng phải vì anh mang một mối tình cảm nào gần '
        'giống với tình yêu dành cho Irene Adler. Mọi cảm xúc, và đặc biệt là thứ tình cảm ấy, '
        'đều xa lạ với bộ óc lạnh lùng, chuẩn xác nhưng cân bằng đến mức đáng kinh ngạc của anh.'
    ),
    "p2": (
        'Một đêm nọ — vào ngày hai mươi tháng Ba năm 1888 — tôi đang trên đường trở về sau chuyến '
        'thăm khám cho một bệnh nhân (bởi lúc bấy giờ tôi đã quay lại hành nghề y dân sự), thì '
        'bước chân đưa tôi đi ngang qua phố Baker. Khi lướt qua cánh cửa thân thuộc vốn luôn gắn '
        'liền trong tâm trí tôi với thời kỳ cầu hôn và những tình tiết u ám của vụ Cuộc Điều Tra '
        'Màu Đỏ, lòng tôi bỗng trào dâng niềm khao khát mãnh liệt được gặp lại Holmes, để xem anh '
        'đang vận dụng năng lực phi thường của mình vào việc gì.'
    ),
    "p3": (
        'Một tiếng bước chân chậm rãi và nặng nề vang lên trên cầu thang rồi dọc theo hành lang, '
        'chợt dừng lại ngay bên ngoài cánh cửa. Tiếp đó là một tiếng gõ cửa lớn đầy uy quyền. '
        '"Mời vào!" Holmes lên tiếng. Bước vào phòng là một người đàn ông cao ngót nghét sáu bộ '
        'sáu phân Anh, mang lồng ngực và tứ chi vạm vỡ tựa thần Hercules. Đưa mắt nhìn qua lại '
        'giữa hai chúng tôi, vị khách cất lời: "Các ông có thể gọi tôi là Bá tước Von Kramm, một '
        'quý tộc xứ Bohemia, dù tôi đến đây để thưa chuyện nhân danh Đức Vua xứ Bohemia."'
    ),
}

INITIAL_BOOK_RECORD = {
    "book_slug": "scandal-in-bohemia",
    "source_lang": "en",
    "target_lang": "vi",
    "genre": "literary",
    "brief": {
        "title_vi": "Vụ Bê Bối Ở Xứ Bohemia",
        "author": "Arthur Conan Doyle",
        "tone": (
            "Văn phong trinh thám cổ điển thời Victoria (cuối thế kỷ 19): lịch lãm, điềm tĩnh, "
            "chuẩn mực, giàu sắc thái quan sát qua lời kể ngôi thứ nhất của Bác sĩ Watson."
        ),
        "pronoun_system": (
            "Ngôi kể thứ nhất của Bác sĩ Watson ('tôi'). Holmes và Watson xưng hô với nhau là "
            "'tôi' - 'anh'. Khách quý hoàng gia (Bá tước Von Kramm / Đức Vua xứ Bohemia) xưng "
            "'ta' hoặc 'tôi' tùy ngữ cảnh trang trọng, gọi Holmes là 'ông' / 'ngài'."
        ),
    },
    "glossary": {
        "Sherlock Holmes": "Sherlock Holmes",
        "Irene Adler": "Irene Adler",
        "Baker Street": "phố Baker",
        "221B Baker Street": "số 221B phố Baker",
        "A Study in Scarlet": "Cuộc Điều Tra Màu Đỏ",
        "Study in Scarlet": "Cuộc Điều Tra Màu Đỏ",
        "Count Von Kramm": "Bá tước Von Kramm",
        "King of Bohemia": "Đức Vua xứ Bohemia",
        "His Majesty the King": "Đức Vua",
        "Bohemia": "xứ Bohemia",
    },
    "character_pronouns": [
        {
            "character_a": "Sherlock Holmes",
            "character_b": "Dr. Watson",
            "a_to_b": "tôi / anh",
            "b_to_a": "tôi / anh",
        }
    ],
}


def translate_ch01(source_xml: str, book_record: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    segs = parse_segments(source_xml)
    blocks = []
    for s in segs:
        s_id = s["id"]
        s_type = s["type"]
        orig = s["original"]
        if s_type == "heading":
            trans = CH01_TRANSLATIONS["heading"]
        elif "she is always the woman" in orig:
            trans = CH01_TRANSLATIONS["p1"]
        elif "twentieth of March, 1888" in orig:
            trans = CH01_TRANSLATIONS["p2"]
        elif "slow and heavy step" in orig:
            trans = CH01_TRANSLATIONS["p3"]
        else:
            trans = orig

        block = (
            f'<segment id="{s_id}" type="{s_type}">\n'
            f'<original>\n{orig}\n</original>\n'
            f'<translate>\n{trans}\n</translate>\n'
            f'</segment>'
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def live_evaluate_fn(source_xml: str, trans_xml: str, book_record: Dict[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        t_dir = Path(td)
        s_p = t_dir / "source.txt"
        d_p = t_dir / "translation.xml"
        b_p = t_dir / "book_record.json"
        o_p = t_dir / "eval.json"
        s_p.write_text(source_xml, encoding="utf-8")
        d_p.write_text(trans_xml, encoding="utf-8")
        b_p.write_text(json.dumps(book_record, ensure_ascii=False, indent=2), encoding="utf-8")
        res = run_evaluation(
            source_path=str(s_p),
            draft_path=str(d_p),
            book_record_path=str(b_p),
            output_path=str(o_p),
            force_mock=False,
            engine="workbuddy",
        )
        return res


def custom_seed_fn(repo_root: Path, book_slug: str):
    print(f"[Pilot Seed] Seeding book '{book_slug}' in {repo_root}...")
    runs_dir = repo_root / "runs" / book_slug
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Initialize book_record.json with Section 3 canonical data
    book_rec_file = runs_dir / "book_record.json"
    if not book_rec_file.exists():
        book_rec_file.write_text(
            json.dumps(INITIAL_BOOK_RECORD, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    res = seed_merged_book(
        repo_root=repo_root,
        book_slug=book_slug,
        repo_full_name="vinhthang/collab-book-translate",
        gh_user="1quachuoi9vang",
        translate_fn=translate_ch01,
        evaluate_fn=live_evaluate_fn,
        git_sha_fn=None,
    )
    print(f"[Pilot Seed] seed_merged_book result: {res}")
    return res


if __name__ == "__main__":
    repo_root = Path(".").resolve()
    print("[Pilot Seed] Running run_sweep with custom_seed_fn on main...")
    sweep_res = run_sweep(
        repo_root=repo_root,
        seed_fn=custom_seed_fn,
        bot_login="1quachuoi9vang",
        human_approver="vinhthang",
    )
    print(f"[Pilot Seed] Sweep completed: {sweep_res}")
