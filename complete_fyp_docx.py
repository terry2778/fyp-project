from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph


def _insert_paragraph_after(paragraph, text: str) -> Paragraph:
    """Insert a paragraph right after the given paragraph."""
    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)  # type: ignore[attr-defined]
    new_para = Paragraph(new_p, paragraph._parent)
    if text:
        new_para.add_run(text)
    return new_para


def _insert_lines_after(paragraph, lines: list[str]) -> None:
    last = paragraph
    for line in lines:
        last = _insert_paragraph_after(last, line)


def complete_report(src: Path, dst: Path) -> None:
    doc = Document(str(src))

    # Track which sections we filled to avoid duplicate insertions on re-run
    inserted_nonfunctional = False
    inserted_plan = False

    paras = list(doc.paragraphs)

    # Pre-locate the "real" Appendix B heading (there may be another one in Table of Content)
    appendix_b_paras = [p for p in paras if (p.text or "").strip() == "Appendix B: Revised Project Plan"]
    appendix_b_para = appendix_b_paras[-1] if appendix_b_paras else None

    for p in paras:
        t = (p.text or "").strip()

        # Fill 3.2 Non-functional Requirement details
        if not inserted_nonfunctional and t == "3.2 Non-functional Requirement":
            # We add concise but complete descriptions after the three headings.
            # Insert after "Performance"
            for p2 in doc.paragraphs:
                if (p2.text or "").strip() == "Performance":
                    _insert_paragraph_after(
                        p2,
                        "The system should complete scraping + preprocessing + sentiment inference for 1,000 reviews within 3–5 minutes on a standard laptop. "
                        "The web dashboard should respond within 2 seconds for common interactions (loading charts, filtering, switching roles).",
                    )
                    break

            # Insert after "Maintainability"
            for p2 in doc.paragraphs:
                if (p2.text or "").strip() == "Maintainability":
                    _insert_paragraph_after(
                        p2,
                        "Adopt a modular architecture (crawler / preprocessing / model / API / UI layers) with clear interfaces and configuration files. "
                        "Provide documentation (README, API docs) and logging so that future team members can update target websites, models, or database schemas with minimal changes.",
                    )
                    break

            # Insert after "User-friendly GUI"
            for p2 in doc.paragraphs:
                if (p2.text or "").strip() == "User-friendly GUI":
                    _insert_paragraph_after(
                        p2,
                        "Provide a simple workflow: paste product URL → choose identity (buyer/seller) → click analyze → view charts and LLM summary. "
                        "Show progress indicators and clear error messages (e.g., login required, network failure) to reduce user confusion.",
                    )
                    break

            inserted_nonfunctional = True

        # Fill Appendix B: Revised Project Plan
        if not inserted_plan and appendix_b_para is not None and p is appendix_b_para:
            plan_lines = [
                "Revised plan (Jan-Apr 2026):",
                "- Week 1-2: Finalize crawler robustness (login handling, anti-bot delays, stable selectors) and expand data collection to >=10,000 reviews.",
                "- Week 3: Complete preprocessing pipeline (regex cleaning, jieba/English tokenization, privacy anonymization) and store processed data in MySQL.",
                "- Week 4-5: Fine-tune and evaluate baseline sentiment models (mBERT / RoBERTa / DistilBERT) using a validation set; select the best model.",
                "- Week 6: Implement topic modeling (LDA as baseline, BERTopic as improved approach) and define product-attribute labels.",
                "- Week 7: Build FastAPI endpoints (submit URL, query results) and integrate with MySQL analysis_results.",
                "- Week 8: Develop the web dashboard (input, role selection, charts, tables) and connect to API.",
                "- Week 9: Integrate LLM-based summary/report generation; design prompts for buyer vs seller personas.",
                "- Week 10: System testing, performance tuning, documentation, and final report/presentation preparation.",
            ]
            _insert_lines_after(p, plan_lines)
            inserted_plan = True

    doc.save(str(dst))


if __name__ == "__main__":
    src = Path(r"c:\Users\yanboyang\Desktop\project\FYP Interim Report.docx")
    dst = Path(r"c:\Users\yanboyang\Desktop\project\FYP Interim Report_completed.docx")
    complete_report(src, dst)
    print(f"WROTE: {dst}")
