from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

ASSET_DIR = Path(__file__).with_name("assets")

BASE_CSS = r"""
@font-face{font-family:"Inter";src:url("fonts/Inter-400-latin.woff2") format("woff2");font-weight:400;font-display:block}
@font-face{font-family:"Inter";src:url("fonts/Inter-700-latin.woff2") format("woff2");font-weight:700;font-display:block}
:root{--bg:#fff;--ink:#0f0f12;--accent:#7C3AED;--soft:#EDE9FE;--muted:#6b6b76;--rule:#e4e4ea}
*{box-sizing:border-box;margin:0;padding:0}html,body{width:100%;height:100%;overflow:hidden;background:#fff;font-family:"Inter",sans-serif}
#stage{position:relative;width:100%;height:100%;overflow:hidden;background:#fff}.video-zone{position:absolute;left:0;top:1312px;width:1080px;height:608px;overflow:hidden;background:#08080b;z-index:1}.video-zone video{width:1080px;height:608px;display:block;object-fit:contain;background:#08080b}
.card-host{position:absolute;pointer-events:none;overflow:hidden}.card-host .card{position:relative;width:100%;height:100%;overflow:hidden}.scene{width:100%;height:100%;position:relative;display:flex;flex-direction:column;padding:44px 48px;background:var(--bg);color:var(--ink)}
.scene.dark{background:var(--ink);color:#fff}.topline{display:flex;align-items:center;gap:14px;min-height:48px}.dot{width:16px;height:16px;background:var(--accent);border-radius:4px}.kicker{font:700 25px/1 "Inter",sans-serif;letter-spacing:.24em}.source-count{margin-left:auto;font:700 17px/1 "Inter",sans-serif;color:var(--accent);background:var(--soft);padding:11px 16px;border-radius:8px}.dark .source-count{background:rgba(255,255,255,.1);color:#fff}
.content{flex:1;display:flex;flex-direction:column;justify-content:space-evenly;position:relative;z-index:2}.title-wrap{position:relative;align-self:flex-start;max-width:100%}.marker{position:absolute;left:-8px;right:-8px;top:58%;height:42%;background:rgba(124,58,237,.34);transform:rotate(-.7deg);z-index:-1}.title{font:700 102px/.96 "Inter",sans-serif;letter-spacing:-.045em;max-width:960px;overflow-wrap:anywhere}.body{font:700 38px/1.25 "Inter",sans-serif;color:var(--muted);max-width:940px}.dark .body{color:rgba(255,255,255,.66)}
.ghost{position:absolute;right:-24px;top:65px;font:700 310px/.8 "Inter",sans-serif;color:rgba(124,58,237,.06);z-index:0}.dark .ghost{color:rgba(255,255,255,.04)}.rule{height:5px;width:180px;background:var(--accent);border-radius:4px}.visual{display:grid;gap:18px;width:100%}.visual-item{background:var(--soft);padding:28px 30px;border-radius:16px;min-height:112px;display:flex;flex-direction:column;justify-content:center;gap:8px}.visual-value{font:700 60px/.95 "Inter",sans-serif;color:var(--accent);letter-spacing:-.035em}.visual-label{font:700 23px/1.2 "Inter",sans-serif;color:var(--ink)}.dark .visual-item{background:rgba(255,255,255,.08)}.dark .visual-label{color:#fff}
.source-row{display:flex;flex-wrap:wrap;gap:10px;border-top:2px solid currentColor;padding-top:18px;opacity:.75}.source-pill{font:700 15px/1 "Inter",sans-serif;letter-spacing:.08em;padding:9px 12px;border:1px solid currentColor;border-radius:999px}
.annotation{position:absolute;right:24px;bottom:76px;width:260px;height:150px}.annotation path{fill:none;stroke:var(--accent);stroke-width:8;stroke-linecap:round;stroke-linejoin:round}
.scene[data-form="hero-stat"] .title{font-size:250px;line-height:.82;color:var(--accent)}.scene[data-form="hero-stat"] .body{font-size:48px;color:var(--ink)}.scene[data-form="hero-stat"] .visual{grid-template-columns:repeat(3,1fr)}
.scene[data-form="comparison"] .visual,.scene[data-form="stat-grid"] .visual{grid-template-columns:repeat(2,1fr)}.scene[data-form="comparison"] .visual-item:nth-child(even){background:var(--accent);color:#fff}.scene[data-form="comparison"] .visual-item:nth-child(even) .visual-value,.scene[data-form="comparison"] .visual-item:nth-child(even) .visual-label{color:#fff}
.scene[data-form="bar-chart"]{background:var(--ink);color:#fff}.scene[data-form="bar-chart"] .body{color:rgba(255,255,255,.6)}.scene[data-form="bar-chart"] .visual{display:flex;flex-direction:column}.scene[data-form="bar-chart"] .visual-item{min-height:92px;display:grid;grid-template-columns:190px 1fr;align-items:center;background:rgba(255,255,255,.06);position:relative;overflow:hidden}.scene[data-form="bar-chart"] .visual-item:after{content:"";position:absolute;left:190px;right:20px;bottom:18px;height:16px;background:var(--accent);border-radius:8px;transform-origin:left}.scene[data-form="bar-chart"] .visual-label{color:#fff}.scene[data-form="bar-chart"] .visual-value{font-size:42px;text-align:right}
.scene[data-form="timeline"] .visual,.scene[data-form="numbered-path"] .visual,.scene[data-form="process-flow"] .visual{display:flex;align-items:stretch}.scene[data-form="timeline"] .visual-item,.scene[data-form="numbered-path"] .visual-item,.scene[data-form="process-flow"] .visual-item{flex:1;position:relative;min-height:210px}.scene[data-form="timeline"] .visual-item:not(:last-child):after,.scene[data-form="numbered-path"] .visual-item:not(:last-child):after,.scene[data-form="process-flow"] .visual-item:not(:last-child):after{content:"→";position:absolute;right:-25px;top:42%;font:700 42px/1 "Inter";color:var(--accent);z-index:5}
.scene[data-form="relationship-map"] .visual{grid-template-columns:repeat(3,1fr);align-items:center}.scene[data-form="relationship-map"] .visual-item{border:3px solid var(--accent);background:#fff;text-align:center}.scene[data-form="relationship-map"] .visual-item:nth-child(2){background:var(--accent);transform:scale(1.08)}.scene[data-form="relationship-map"] .visual-item:nth-child(2) *{color:#fff}
.scene[data-form="evidence-board"]{background:var(--ink);color:#fff}.scene[data-form="evidence-board"] .body{color:rgba(255,255,255,.65)}.scene[data-form="evidence-board"] .visual{grid-template-columns:repeat(2,1fr)}.scene[data-form="evidence-board"] .visual-item{background:#fff;border-left:12px solid var(--accent)}
.scene[data-form="myth-strikethrough"] .title{font-size:118px}.scene[data-form="myth-strikethrough"] .title:after{content:"";position:absolute;left:-12px;right:-12px;top:48%;height:16px;background:var(--accent);transform:rotate(-1.4deg)}
.scene[data-form="question"]{background:var(--accent);color:#fff;text-align:center}.scene[data-form="question"] .content{align-items:center}.scene[data-form="question"] .title{font-size:100px}.scene[data-form="question"] .marker{background:rgba(0,0,0,.16)}.scene[data-form="question"] .body{color:rgba(255,255,255,.82)}
.scene[data-form="quote"] .title:before{content:"“";font-size:200px;color:var(--accent);position:absolute;left:-20px;top:-130px}.scene[data-form="labeled-diagram"] .visual{grid-template-columns:1fr 1fr 1fr}.scene[data-form="editorial-statement"] .title{font-size:126px}.scene[data-form="editorial-statement"] .visual{grid-template-columns:repeat(3,1fr)}
.caption{position:absolute;left:70px;top:1738px;width:940px;min-height:100px;display:flex;align-items:center;justify-content:center;padding:16px 26px;background:rgba(15,15,18,.88);border-radius:18px;color:#fff;font:700 38px/1.2 "Inter",sans-serif;text-align:center;z-index:20;pointer-events:none}
"""


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _visual_items(scene: dict) -> list[tuple[str, str]]:
    points = []
    for point in scene.get("dataPoints", [])[:6]:
        if isinstance(point, dict):
            points.append((_esc(point.get("value", "")), _esc(point.get("label", ""))))
    if points:
        return points
    for index, element in enumerate(scene.get("primaryElements", [])[:6]):
        points.append((f"{index + 1:02d}", _esc(element)))
    if not points:
        points = [("01", "SOURCE"), ("02", "CONTEXT"), ("03", "TAKEAWAY")]
    return points


def _scene_html(scene: dict, index: int) -> str:
    scene_id = _esc(scene["id"])
    start = float(scene["startSec"])
    end = float(scene["endSec"])
    duration = max(0.001, end - start)
    form = _esc(scene.get("visualForm", "editorial-statement"))
    dark = " dark" if form in {"bar-chart", "evidence-board"} else ""
    items = "".join(
        f'<div class="visual-item"><div class="visual-value">{value}</div><div class="visual-label">{label}</div></div>'
        for value, label in _visual_items(scene)
    )
    sources = "".join(
        f'<span class="source-pill">SOURCE { _esc(tweet_id) }</span>'
        for tweet_id in scene.get("evidenceTweetIds", [])[:4]
    )
    if not sources:
        sources = '<span class="source-pill">APPROVED SCRIPT</span>'
    return f'''<div id="card-{scene_id}" class="card-host clip" data-card-id="{scene_id}" data-start="{start:.3f}" data-duration="{duration:.3f}" data-track-index="2" style="left:0;top:0;width:1080px;height:1312px;visibility:hidden;opacity:0">
<div class="card" data-card-id="{scene_id}"><div class="scene{dark}" data-form="{form}"><div class="ghost">{index + 1:02d}</div>
<div class="topline"><span class="dot"></span><span class="kicker">{_esc(scene.get('kicker', 'THE STORY'))}</span><span class="source-count">{len(scene.get('evidenceTweetIds', []))} SOURCE</span></div>
<div class="content"><div class="title-wrap"><span class="marker"></span><h2 class="title">{_esc(scene.get('title', ''))}</h2></div><p class="body">{_esc(scene.get('body', ''))}</p><div class="rule"></div><div class="visual">{items}</div></div>
<div class="source-row">{sources}</div><svg class="annotation" viewBox="0 0 260 150"><path d="M18 92 C70 18 174 12 236 70 C190 130 84 142 28 104 C8 88 42 48 96 32"/></svg></div></div></div>'''


def _caption_groups(words: list[dict], maximum_words: int = 7) -> list[dict]:
    groups = []
    for offset in range(0, len(words), maximum_words):
        chunk = words[offset : offset + maximum_words]
        if not chunk:
            continue
        groups.append(
            {
                "text": " ".join(str(word.get("text", "")).strip() for word in chunk).strip(),
                "start": float(chunk[0].get("start", 0.0)),
                "end": float(chunk[-1].get("end", 0.0)),
            }
        )
    return groups


def _captions_html(words: list[dict], duration: float) -> str:
    blocks = []
    for index, group in enumerate(_caption_groups(words)):
        start = max(0.0, min(duration, group["start"]))
        end = max(start + 0.05, min(duration, group["end"]))
        blocks.append(
            f'<div id="caption-{index + 1:02d}" class="caption clip" data-start="{start:.3f}" data-duration="{end - start:.3f}" data-track-index="20" style="visibility:hidden">{_esc(group["text"])}</div>'
        )
    return "\n".join(blocks)


def _timeline_js(storyboard: dict) -> str:
    lines = [
        '(function(){var Q=function(s){return Math.round(s*30)/30};var tl=window.gsap.timeline({paused:true});'
    ]
    for scene in storyboard.get("scenes", []):
        start = float(scene["startSec"])
        end = float(scene["endSec"])
        duration = end - start
        exit_at = max(start, end - min(0.3, duration * 0.18))
        selector = f'.card-host[data-card-id="{scene["id"]}"]'
        lines.extend(
            [
                f"var s={json.dumps(selector)};tl.set(s,{{visibility:'visible'}},Q({start}));",
                f"tl.fromTo(s,{{opacity:0}},{{opacity:1,duration:{min(0.4, duration * 0.2):.3f},ease:'power2.out'}},Q({start}));",
                f"tl.fromTo(s+' .title',{{opacity:0,y:28}},{{opacity:1,y:0,duration:.5,ease:'power2.out'}},Q({start + min(0.25, duration * 0.12)}));",
                f"tl.fromTo(s+' .marker',{{scaleX:0,transformOrigin:'left'}},{{scaleX:1,duration:.45,ease:'power2.inOut'}},Q({start + min(0.7, duration * 0.3)}));",
                f"tl.fromTo(s+' .visual-item',{{opacity:0,y:24}},{{opacity:1,y:0,duration:.45,stagger:.12,ease:'power2.out'}},Q({start + min(0.9, duration * 0.38)}));",
                f"tl.fromTo(s+' .annotation path',{{strokeDasharray:700,strokeDashoffset:700}},{{strokeDashoffset:0,duration:.7,ease:'power2.inOut'}},Q({start + min(1.25, duration * 0.48)}));",
                f"tl.to(s,{{opacity:0,duration:{min(0.3, duration * 0.18):.3f},ease:'power2.in'}},Q({exit_at}));",
                f"tl.set(s,{{visibility:'hidden'}},Q({end}));",
            ]
        )
    lines.append("window.__timelines=window.__timelines||{};window.__timelines['crypto-news']=tl;})();")
    return "".join(lines)


OVERFLOW_JS = r"""
(function(){
  function checkOverflow(){
    var issues=[];
    document.querySelectorAll('.content,.source-row,.caption').forEach(function(el){
      var scrolls=el.scrollWidth>el.clientWidth+1 || el.scrollHeight>el.clientHeight+1;
      if(scrolls){
        issues.push({id:el.id||'',className:el.className||'',scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight});
      }
    });
    document.querySelectorAll('.title,.body,.visual-value,.visual-label,.kicker,.source-pill').forEach(function(el){
      if(el.scrollWidth>el.clientWidth+1){
        issues.push({id:el.id||'',className:el.className||'',scrollWidth:el.scrollWidth,clientWidth:el.clientWidth});
      }
    });
    var report={count:issues.length,issues:issues};
    document.documentElement.setAttribute('data-text-overflow-count',String(report.count));
    document.documentElement.setAttribute('data-text-overflow-report',encodeURIComponent(JSON.stringify(report)));
    console.log('HF_TEXT_OVERFLOW:'+JSON.stringify(report));
  }
  window.addEventListener('load',function(){
    if(document.fonts && document.fonts.ready){document.fonts.ready.then(checkOverflow);}
    else{checkOverflow();}
  });
  window.setTimeout(checkOverflow,750);
})();
"""


def _stage_assets(public_dir: Path) -> None:
    font_dir = public_dir / "fonts"
    vendor_dir = public_dir / "vendor"
    font_dir.mkdir(parents=True, exist_ok=True)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    required = [
        (ASSET_DIR / "Inter-400-latin.woff2", font_dir / "Inter-400-latin.woff2"),
        (ASSET_DIR / "Inter-700-latin.woff2", font_dir / "Inter-700-latin.woff2"),
        (ASSET_DIR / "gsap.min.js", vendor_dir / "gsap.min.js"),
    ]
    for source, destination in required:
        if not source.exists():
            raise FileNotFoundError(f"Missing bundled video asset: {source}")
        shutil.copy2(source, destination)


def write_composition(public_dir: str | Path, storyboard: dict, transcript_words: list[dict]) -> Path:
    public = Path(public_dir)
    public.mkdir(parents=True, exist_ok=True)
    _stage_assets(public)
    composition = storyboard["composition"]
    duration = float(composition["durationSeconds"])
    cards = "\n".join(
        _scene_html(scene, index) for index, scene in enumerate(storyboard.get("scenes", []))
    )
    captions = (
        _captions_html(transcript_words, duration)
        if composition.get("captionsEnabled")
        else ""
    )
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{BASE_CSS}</style></head><body>
<div id="stage" data-composition-id="crypto-news" data-start="0" data-duration="{duration:.3f}" data-fps="30" data-width="1080" data-height="1920">
<div class="video-zone"><video id="speaker-video" src="input-video.mp4" muted playsinline data-start="0" data-duration="{duration:.3f}" data-track-index="1"></video></div>
<audio id="source-audio" src="input-video.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
{cards}
{captions}
<script src="vendor/gsap.min.js"></script><script>{_timeline_js(storyboard)}{OVERFLOW_JS}</script>
</div></body></html>'''
    index_path = public / "index.html"
    index_path.write_text(document, encoding="utf-8")
    return index_path
