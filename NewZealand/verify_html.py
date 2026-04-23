import sys
sys.stdout.reconfigure(encoding="utf-8")

with open("frontend/static/index.html", encoding="utf-8") as f:
    html = f.read()

checks = [
    ("v=10 css", "?v=10" in html),
    ("v=10 js", "app.js?v=10" in html),
    ("tab underline style", "border-bottom-color" in html),
    ("topbar stretch", "align-items:stretch" in html),
    ("guide banner", "analysis-guide" in html),
    ("국가 GDP label", "국가 GDP" in html),
    ("population 5127000", "5,127,000" in html),
    ("GDP US$ 253.5B", "US$ 253.5 Billion" in html),
    ("수입 의존도 label", "의약품 국가 수입 의존도" in html),
    ("~90% value", "~90%" in html),
    ("KOTRA source", "KOTRA / ITA (2024)" in html),
    ("NZ map section", "nz-map" in html),
    ("pill box-shadow", "box-shadow:0 1px 6px" in html),
    ("page-tab.active underline", ".page-tab.active" in html),
    ("Perplexity news endpoint", "loadNews" in html),
    ("dual-market pub section", "p2-result-pub" in html),
    ("dual-market pri section", "p2-result-pri" in html),
    ("price edit modal", "p2-edit-modal" in html),
    ("modal apply button", "applyModalAndClose" in html),
    ("되돌리기 button", "resetModalToAiDefaults" in html),
    ("기준 label (not 기준가)", 'p2-col-label">기준<' in html),
    ("no 기준가 label", 'p2-col-label">기준가<' not in html),
    ("openP2EditModal", "openP2EditModal" in html),
    ("pub-agg col", "p2c-price-pub-agg" in html),
    ("pri-cons col", "p2c-price-pri-cons" in html),
]

all_ok = True
for name, result in checks:
    status = "OK  " if result else "FAIL"
    print(f"  {status}  {name}")
    if not result:
        all_ok = False

print()
print("All checks passed!" if all_ok else "Some checks FAILED!")
