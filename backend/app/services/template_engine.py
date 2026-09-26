import hashlib
import os
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment


TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


class ConditionalUndefined(StrictUndefined):
    """Strict about output, lenient in conditionals.

    Printing an undefined variable still fails loudly (§3.3.38: no
    unresolved production placeholders), but testing one in an ``{% if %}``
    evaluates falsy so templates can keep gating optional sections with
    bare ``{% if optional_var %}`` — matching how the seeded template
    catalog is written.
    """

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) or other is None

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return id(self)


def get_jinja_env() -> SandboxedEnvironment:
    """Sandboxed template environment (spec §3.3.12).

    Templates may only use safe Jinja constructs — attribute access through
    the sandbox can never reach ``__class__``/``__subclasses__`` style escapes,
    and ConditionalUndefined keeps unresolved variables loud in output
    (§3.3.38) while letting conditionals test them safely.
    """
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape([]),
        undefined=ConditionalUndefined,
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
