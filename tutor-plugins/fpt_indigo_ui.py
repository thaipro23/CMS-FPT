from __future__ import annotations

from tutor import hooks
from tutormfe.hooks import MFE_APPS, MFE_ATTRS_TYPE, PLUGIN_SLOTS

FPT_PRIMARY = "#0B3B82"
FPT_ACCENT = "#F97316"

FPT_LOGO_SOURCE = "https://images.seeklogo.com/logo-png/64/1/cao-ng-fpt-polytechnic-logo-png_seeklogo-648612.png"
FPT_BANNER_STUDENTS_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/H1-113.jpg"
FPT_BANNER_HANOI_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/1-255.jpg"
FPT_BANNER_CAMPUS_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/Nhiep-anh-cong-trinh_03_TOA-NHA-FPL-HCM-1.jpg"


def _jinja_raw(text: str) -> str:
    """Protect JSX/CSS braces from Tutor/Jinja patch rendering."""
    return "{% raw %}\n" + text + "\n{% endraw %}"


hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-common-settings",
    _jinja_raw(f"""
MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False
MFE_CONFIG["INDIGO_FOOTER_NAV_LINKS"] = []
MFE_CONFIG["ALLOW_PUBLIC_ACCOUNT_CREATION"] = False
MFE_CONFIG["SHOW_REGISTRATION_LINKS"] = False
MFE_CONFIG["SITE_NAME"] = "FPT Polytechnic"
MFE_CONFIG["FPT_PRIMARY_COLOR"] = "{FPT_PRIMARY}"
MFE_CONFIG["FPT_ACCENT_COLOR"] = "{FPT_ACCENT}"
"""),
))


hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-post-npm-install-authn",
    _jinja_raw(r"""
RUN python - <<'PY2'
from pathlib import Path

messages = Path('/openedx/app/src/base-container/components/default-layout/messages.js')
if messages.exists():
    text = messages.read_text(encoding='utf-8')
    text = text.replace("defaultMessage: 'Start learning'", "defaultMessage: 'Học tập cùng'")
    text = text.replace("defaultMessage: 'with {siteName}'", "defaultMessage: '{siteName}'")
    messages.write_text(text, encoding='utf-8')

large = Path('/openedx/app/src/base-container/components/default-layout/LargeLayout.jsx')
if large.exists():
    text = large.read_text(encoding='utf-8')
    needle = '<div className="col-md-9 bg-primary-400">'
    replacement = '''<div
          className="col-md-9 bg-primary-400 fpt-auth-photo"
          style={{
            backgroundImage: `linear-gradient(90deg, rgba(7,43,97,.94) 0%, rgba(11,59,130,.84) 45%, rgba(11,59,130,.50) 100%), url(${getConfig().LMS_BASE_URL}/static/indigo/images/fpt/fpt-students.jpg)`,
          }}
        >'''
    if needle in text and 'fpt-auth-photo' not in text:
        large.write_text(text.replace(needle, replacement, 1), encoding='utf-8')

medium = Path('/openedx/app/src/base-container/components/default-layout/MediumLayout.jsx')
if medium.exists():
    text = medium.read_text(encoding='utf-8')
    needle = '<div className="col-md-10 bg-primary-400">'
    replacement = '''<div
          className="col-md-10 bg-primary-400 fpt-auth-photo"
          style={{
            backgroundImage: `linear-gradient(90deg, rgba(7,43,97,.94) 0%, rgba(11,59,130,.72) 100%), url(${getConfig().LMS_BASE_URL}/static/indigo/images/fpt/fpt-students.jpg)`,
          }}
        >'''
    if needle in text and 'fpt-auth-photo' not in text:
        medium.write_text(text.replace(needle, replacement, 1), encoding='utf-8')

scss = Path('/openedx/app/src/index.scss')
if scss.exists():
    marker = 'FPT Polytechnic branding overlay'
    branding = '''
/* FPT Polytechnic branding overlay. Authentication behavior remains upstream. */
:root { --fpt-primary:#0B3B82; --fpt-primary-dark:#072B61; --fpt-accent:#F97316; }
.btn-primary { background:#0B3B82 !important; border-color:#0B3B82 !important; }
.btn-primary:hover,.btn-primary:focus { background:#072B61 !important; border-color:#072B61 !important; }
.text-accent-a { color:#F97316 !important; }
.bg-primary-400,.bg-primary-500 { background-color:#0B3B82 !important; }
.fpt-auth-photo {
  position:relative;
  overflow:hidden;
  background-size:cover !important;
  background-position:center !important;
  background-repeat:no-repeat !important;
}
.fpt-auth-photo::after {
  content:'';
  position:absolute;
  inset:0;
  background:linear-gradient(180deg,rgba(0,0,0,.04),rgba(0,0,0,.16));
  pointer-events:none;
}
.fpt-auth-photo > * { position:relative; z-index:1; }
.layout .content { background:#F7F9FC; }
#main-content {
  background:#fff;
  border:1px solid #E7EBF2;
  border-radius:18px;
  box-shadow:0 18px 48px rgba(11,59,130,.08);
  padding:32px;
}
@media (max-width:767.98px) {
  #main-content { padding:22px; border-radius:14px; }
}
'''
    current = scss.read_text(encoding='utf-8')
    if marker not in current:
        scss.write_text(current + '\n' + branding, encoding='utf-8')
PY2
"""),
))


hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-runtime-definitions",
    _jinja_raw(r"""
const getFptAsset = (name) => `${getConfig().LMS_BASE_URL}/static/indigo/images/fpt/${name}`;

const FptInlineLogo = ({ compact = false }) => (
  <img
    src={getFptAsset('fpt-polytechnic-logo.png')}
    alt="FPT Polytechnic"
    style={{
      display: 'block',
      width: compact ? 172 : 215,
      height: compact ? 42 : 54,
      objectFit: 'contain',
      objectPosition: 'left center',
    }}
  />
);

const FptHeaderLogo = () => {
  const baseUrl = getConfig().LMS_BASE_URL;
  return (
    <a href={`${baseUrl}/dashboard`} aria-label="FPT Polytechnic" className="logo" style={{ textDecoration: 'none' }}>
      <FptInlineLogo compact />
    </a>
  );
};

const FptFooter = () => {
  useEffect(() => {
    ['selected-paragon-theme-variant', 'selected-theme-variant'].forEach((name) => {
      window.localStorage.setItem(name, 'light');
    });
    document.documentElement.setAttribute('data-paragon-theme-variant', 'light');
    document.documentElement.setAttribute('data-theme-variant', 'light');
  }, []);

  return (
    <footer className="fpt-ui-footer">
      <div className="container-fluid fpt-ui-footer__container">
        <div className="fpt-ui-footer__grid">
          <div><FptInlineLogo /></div>
          <div>
            <div className="fpt-ui-footer__title">THÔNG TIN LIÊN HỆ</div>
            <a href="mailto:caodang@fpt.edu.vn">caodang@fpt.edu.vn</a>
          </div>
          <div>
            <div className="fpt-ui-footer__title">Trụ sở chính</div>
            <div className="fpt-ui-footer__address">Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br />Phường Xuân Phương, TP Hà Nội</div>
          </div>
        </div>
        <div className="fpt-ui-footer__copyright">© FPT Polytechnic. All rights reserved.</div>
      </div>
      <style>{`
        .fpt-ui-footer{border-top:1px solid #E7EBF2;background:#fff;margin-top:40px}
        .fpt-ui-footer__container{max-width:1320px;padding:34px 24px 26px}
        .fpt-ui-footer__grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.4fr);gap:32px}
        .fpt-ui-footer__title{color:#0B3B82;font-weight:800;margin-bottom:10px}
        .fpt-ui-footer a{color:#26364A;text-decoration:none}
        .fpt-ui-footer__address{color:#445468;line-height:1.7}
        .fpt-ui-footer__copyright{border-top:1px solid #EEF1F5;margin-top:26px;padding-top:18px;color:#718096;font-size:13px;text-align:center}
        @media(max-width:767px){.fpt-ui-footer__grid{grid-template-columns:1fr;gap:22px}}
      `}</style>
    </footer>
  );
};

const FptLearnerBanner = () => (
  <div className="fpt-learner-banner">
    <div className="fpt-learner-banner__content">
      <div className="fpt-learner-banner__title">Tiếp tục hành trình học tập</div>
      <div className="fpt-learner-banner__text">Chọn một khóa học bên dưới để tiếp tục học tập trên FPT Polytechnic.</div>
    </div>
    <style>{`
      .fpt-learner-banner{position:relative;overflow:hidden;min-height:150px;border:1px solid #E6ECF5;border-radius:14px;margin-bottom:24px;box-shadow:0 8px 24px rgba(11,59,130,.05);background:linear-gradient(90deg,rgba(255,255,255,.98) 0%,rgba(255,247,237,.94) 58%,rgba(255,247,237,.18) 100%),url(${getFptAsset('fpt-hanoi-campus.jpg')}) center 43%/cover no-repeat}
      .fpt-learner-banner__content{max-width:650px;padding:28px 30px;position:relative;z-index:1}
      .fpt-learner-banner__title{color:#0B3B82;font-size:24px;font-weight:800;margin-bottom:6px}
      .fpt-learner-banner__text{color:#58677A;line-height:1.55}
    `}</style>
  </div>
);
"""),
))


FPT_FOOTER_SLOT = (
    "org.openedx.frontend.layout.footer.v1",
    """
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'indigo_footer' },
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'default_contents' },
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_footer', type: DIRECT_PLUGIN, priority: 100, RenderWidget: FptFooter } },
""",
)

FPT_LOGO_SLOT = (
    "logo_slot",
    """
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'custom_logo' },
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'default_contents' },
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_logo', type: DIRECT_PLUGIN, priority: 100, RenderWidget: FptHeaderLogo } },
""",
)

FPT_HIDE_THEME_TOGGLE = (
    "desktop_secondary_menu_slot",
    "{ op: PLUGIN_OPERATIONS.Hide, widgetId: 'theme_switch_button' },",
)

for _mfe in ["learning", "learner-dashboard", "profile", "account", "discussions", "authoring", "authn"]:
    PLUGIN_SLOTS.add_item((_mfe, *FPT_FOOTER_SLOT))
    PLUGIN_SLOTS.add_item((_mfe, *FPT_HIDE_THEME_TOGGLE))

PLUGIN_SLOTS.add_item((
    "learner-dashboard",
    "org.openedx.frontend.learner_dashboard.course_list.v1",
    """
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_learner_banner', type: DIRECT_PLUGIN, priority: 1, RenderWidget: FptLearnerBanner } },
""",
))


@MFE_APPS.add()
def _fpt_brand_all_mfes(mfes: dict[str, MFE_ATTRS_TYPE]) -> dict[str, MFE_ATTRS_TYPE]:
    for mfe in mfes:
        PLUGIN_SLOTS.add_item((str(mfe), *FPT_LOGO_SLOT))
    return mfes


hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    _jinja_raw(f"""
RUN mkdir -p /openedx/staticfiles/indigo/images/fpt \\
    && curl -fL --retry 3 -A 'Mozilla/5.0' '{FPT_LOGO_SOURCE}' -o /openedx/staticfiles/indigo/images/fpt/fpt-polytechnic-logo.png \\
    && curl -fL --retry 3 -A 'Mozilla/5.0' '{FPT_BANNER_STUDENTS_SOURCE}' -o /openedx/staticfiles/indigo/images/fpt/fpt-students.jpg \\
    && curl -fL --retry 3 -A 'Mozilla/5.0' '{FPT_BANNER_HANOI_SOURCE}' -o /openedx/staticfiles/indigo/images/fpt/fpt-hanoi-campus.jpg \\
    && curl -fL --retry 3 -A 'Mozilla/5.0' '{FPT_BANNER_CAMPUS_SOURCE}' -o /openedx/staticfiles/indigo/images/fpt/fpt-campus.jpg

RUN python - <<'PY2'
from pathlib import Path

courses = Path('/openedx/themes/indigo/lms/templates/courseware/courses.html')
if courses.exists():
    text = courses.read_text(encoding='utf-8')
    if 'id="fpt-hero-slider"' not in text:
        anchor = '<section class="courses-container">'
        hero = '''
<section id="fpt-hero-slider" class="fpt-hero" aria-label="FPT Polytechnic">
<style>
.fpt-hero{{position:relative;overflow:hidden;margin:0 0 28px;background:#0B3B82;color:#fff;min-height:330px;box-shadow:0 14px 34px rgba(11,59,130,.12)}}
.fpt-slide{{display:none;position:relative;min-height:330px;align-items:center;background:#0B3B82}}
.fpt-slide.is-active{{display:flex}}
.fpt-slide__photo{{position:absolute;inset:0 0 0 48%;overflow:hidden}}
.fpt-slide__photo:after{{content:'';position:absolute;inset:0;background:linear-gradient(90deg,#0B3B82 0%,rgba(11,59,130,.62) 25%,rgba(11,59,130,.12) 70%)}}
.fpt-slide__photo img{{width:100%;height:100%;object-fit:cover;display:block}}
.fpt-slide__content{{width:58%;padding:52px 0 60px 7%;position:relative;z-index:2}}
.fpt-slide h1{{margin:0 0 14px;color:#fff;font-size:42px;line-height:1.12;font-weight:800;letter-spacing:-.025em}}
.fpt-slide p{{margin:0 0 24px;max-width:570px;font-size:18px;line-height:1.55;color:rgba(255,255,255,.92)}}
.fpt-slide a{{display:inline-block;background:#F97316;color:#fff!important;padding:12px 20px;border-radius:7px;font-weight:700;text-decoration:none}}
.fpt-slide a:hover{{background:#E9640C}}
.fpt-hero__nav{{position:absolute;z-index:4;left:50%;bottom:18px;transform:translateX(-50%);display:flex;gap:8px}}
.fpt-dot{{width:9px;height:9px;border:0;border-radius:50%;background:rgba(255,255,255,.45);padding:0;cursor:pointer}}
.fpt-dot.is-active{{width:24px;border-radius:8px;background:#fff}}
@media(max-width:900px){{.fpt-slide__photo{{inset:0;opacity:.34}}.fpt-slide__photo:after{{background:linear-gradient(90deg,rgba(11,59,130,.98),rgba(11,59,130,.68))}}.fpt-slide__content{{width:78%;padding-left:6%}}.fpt-slide h1{{font-size:34px}}}}
@media(max-width:600px){{.fpt-hero,.fpt-slide{{min-height:300px}}.fpt-slide__content{{width:100%;padding:38px 24px 55px}}.fpt-slide h1{{font-size:30px}}.fpt-slide p{{font-size:16px}}}}
</style>
<div class="fpt-slide is-active"><div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-students.jpg" alt="Sinh viên FPT Polytechnic"></div><div class="fpt-slide__content"><h1>Học tập cùng FPT Polytechnic</h1><p>Nâng cao kiến thức, phát triển kỹ năng và tiếp tục hành trình học tập trên nền tảng số.</p><a href="#discovery-form">Khám phá khóa học</a></div></div>
<div class="fpt-slide"><div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-hanoi-campus.jpg" alt="FPT Polytechnic Hà Nội"></div><div class="fpt-slide__content"><h1>Học mọi lúc, mọi nơi</h1><p>Truy cập khóa học, nội dung và hoạt động học tập thuận tiện trên nhiều thiết bị.</p><a href="#discovery-form">Tìm khóa học</a></div></div>
<div class="fpt-slide"><div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-campus.jpg" alt="Không gian FPT Polytechnic"></div><div class="fpt-slide__content"><h1>Sẵn sàng cho tương lai</h1><p>Môi trường học tập hiện đại, tập trung vào trải nghiệm sinh viên và năng lực nghề nghiệp.</p><a href="#discovery-form">Bắt đầu ngay</a></div></div>
<div class="fpt-hero__nav"><button class="fpt-dot is-active" type="button" aria-label="Slide 1"></button><button class="fpt-dot" type="button" aria-label="Slide 2"></button><button class="fpt-dot" type="button" aria-label="Slide 3"></button></div>
<script>
(function(){{
  var root=document.getElementById('fpt-hero-slider');if(!root)return;
  var slides=root.querySelectorAll('.fpt-slide'),dots=root.querySelectorAll('.fpt-dot'),i=0,timer;
  function show(n){{i=(n+slides.length)%slides.length;slides.forEach(function(s,x){{s.classList.toggle('is-active',x===i)}});dots.forEach(function(d,x){{d.classList.toggle('is-active',x===i)}})}}
  function start(){{timer=setInterval(function(){{show(i+1)}},6500)}}
  dots.forEach(function(d,x){{d.addEventListener('click',function(){{clearInterval(timer);show(x);start()}})}});
  root.addEventListener('mouseenter',function(){{clearInterval(timer)}});
  root.addEventListener('mouseleave',start);
  start();
}})();
</script>
</section>
'''
        if anchor in text:
            courses.write_text(text.replace(anchor, anchor + hero, 1), encoding='utf-8')

footer = Path('/openedx/themes/indigo/lms/templates/footer.html')
if footer.exists():
    footer.write_text('''
<footer class="fpt-lms-footer" style="border-top:1px solid #E7EBF2;background:#fff;margin-top:40px">
  <div style="max-width:1320px;margin:0 auto;padding:34px 24px 26px;display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.4fr);gap:32px">
    <div><img src="/static/indigo/images/fpt/fpt-polytechnic-logo.png" alt="FPT Polytechnic" style="width:215px;max-height:58px;object-fit:contain;object-position:left center"></div>
    <div><strong style="color:#0B3B82">THÔNG TIN LIÊN HỆ</strong><p><a href="mailto:caodang@fpt.edu.vn">caodang@fpt.edu.vn</a></p></div>
    <div><strong style="color:#0B3B82">Trụ sở chính</strong><p style="line-height:1.7">Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br/>Phường Xuân Phương, TP Hà Nội</p></div>
  </div>
  <div style="max-width:1320px;margin:0 auto;border-top:1px solid #EEF1F5;padding:18px 24px 24px;text-align:center;color:#718096;font-size:13px">© FPT Polytechnic. All rights reserved.</div>
</footer>
''', encoding='utf-8')
PY2
"""),
))
