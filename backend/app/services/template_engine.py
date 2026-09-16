import hashlib
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


def get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape([]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def get_available_templates() -> list[dict]:
    templates = []
    for f in TEMPLATE_DIR.glob("*.jinja2"):
        templates.append({
            "key": f.stem,
            "filename": f.name,
            "path": str(f),
        })
    return templates


def render_template(template_key: str, answers: dict) -> str:
    env = get_jinja_env()
    template = env.get_template(f"{template_key}.jinja2")
    return template.render(**answers)


def calculate_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def render_to_html(template_key: str, answers: dict) -> str:
    rendered_text = render_template(template_key, answers)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agreement</title>
    <style>
        body {{
            font-family: 'Times New Roman', serif;
            font-size: 12pt;
            line-height: 1.6;
            margin: 40px;
            color: #000;
        }}
        h1 {{
            text-align: center;
            font-size: 16pt;
            margin-bottom: 30px;
            text-transform: uppercase;
        }}
        p {{
            margin-bottom: 12px;
            text-align: justify;
        }}
        .section {{
            margin-bottom: 20px;
        }}
        .signature-block {{
            margin-top: 40px;
            page-break-inside: avoid;
        }}
        .signature-line {{
            margin-top: 40px;
            margin-bottom: 10px;
        }}
    </style>
</head>
<body>
    <pre style="white-space: pre-wrap; font-family: 'Times New Roman', serif; font-size: 12pt;">{rendered_text}</pre>
</body>
</html>"""

    return html
