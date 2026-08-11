from __future__ import annotations

from tutor import hooks
from tutormfe.hooks import MFE_APPS, MFE_ATTRS_TYPE, PLUGIN_SLOTS

FPT_PRIMARY = "#0B3B82"
FPT_ACCENT = "#F97316"

# Asset sources selected for the FPT branding layer.
# Logo shape/usage was checked against FPT Polytechnic's official 2024 logo guideline.
# The banner photographs below are hosted by the official FPT Polytechnic website.
FPT_LOGO_SOURCE = "https://images.seeklogo.com/logo-png/64/1/cao-ng-fpt-polytechnic-logo-png_seeklogo-648612.png"
FPT_BANNER_STUDENTS_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/H1-113.jpg"
FPT_BANNER_HANOI_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/1-255.jpg"
FPT_BANNER_CAMPUS_SOURCE = "https://caodang.fpt.edu.vn/wp-content/uploads/Nhiep-anh-cong-trinh_03_TOA-NHA-FPL-HCM-1.jpg"


# -----------------------------------------------------------------------------
# Runtime config
# -----------------------------------------------------------------------------
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-common-settings",
    f"""
MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False
MFE_CONFIG["INDIGO_FOOTER_NAV_LINKS"] = []
MFE_CONFIG["ALLOW_PUBLIC_ACCOUNT_CREATION"] = False
MFE_CONFIG["SHOW_REGISTRATION_LINKS"] = False
MFE_CONFIG["SITE_NAME"] = "FPT Polytechnic"
MFE_CONFIG["FPT_PRIMARY_COLOR"] = "{FPT_PRIMARY}"
MFE_CONFIG["FPT_ACCENT_COLOR"] = "{FPT_ACCENT}"
""",
))


# -----------------------------------------------------------------------------
# Authn MFE
# Keep upstream form + FEID/SSO logic, change only branding/layout presentation.
# -----------------------------------------------------------------------------
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-post-npm-install-authn",
    r"""
RUN python - <<'PY2'
from pathlib import Path

messages = Path('/openedx/app/src/base-container/components/default-layout/messages.js')
if messages.exists():
    text = messages.read_text(encoding='utf-8')
    text = text.replace("defaultMessage: 'Start learning'", "defaultMessage: 'Học tập cùng'")
    text = text.replace("defaultMessage: 'with {siteName}'", "defaultMessage: '{siteName}'")
    messages.write_text(text, encoding='utf-8')

# Desktop login hero: use a real FPT Polytechnic student photo served locally by LMS.
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
        text = text.replace(needle, replacement, 1)
        large.write_text(text, encoding='utf-8')

# Tablet login hero uses the same official photo while preserving upstream layout.
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
        text = text.replace(needle, replacement, 1)
        medium.write_text(text, encoding='utf-8')

scss = Path('/openedx/app/src/index.scss')
if scss.exists():
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
@media (max-width: 767.98px) {
  #main-content { padding:22px; border-radius:14px; }
}
'''
    if 'FPT Polytechnic branding overlay' not in scss.read_text(encoding='utf-8'):
        scss.write_text(scss.read_text(encoding='utf-8') + '\n' + branding, encoding='utf-8')
PY2
""",
))


# -----------------------------------------------------------------------------
# Runtime React components for Indigo MFE slots
# -----------------------------------------------------------------------------
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-runtime-definitions",
    r"""
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
    ['selected-paragon-theme-variant', 'selected-theme-variant'].forEach(name => {
      window.localStorage.setItem(name, 'light');
    });
    document.documentElement.setAttribute('data-paragon-theme-variant', 'light');
    document.documentElement.setAttribute('data-theme-variant', 'light');
  }, []);

  return (
    <footer style={{ borderTop: '1px solid #E7EBF2', background: '#fff', marginTop: 40 }}>
      <div className="container-fluid" style={{ maxWidth: 1320, padding: '34px 24px 26px' }}>
        <div className="fpt-footer-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.4fr)', gap: 32 }}>
          <div><FptInlineLogo /></div>
          <div>
            <div style={{ color: '#0B3B82', fontWeight: 800, marginBottom: 10 }}>THÔNG TIN LIÊN HỆ</div>
            <a href="mailto:caodang@fpt.edu.vn" style={{ color: '#26364A', textDecoration: 'none' }}>caodang@fpt.edu.vn</a>
          </div>
          <div>
            <div style={{ color: '#0B3B82', fontWeight: 800, marginBottom: 10 }}>Trụ sở chính</div>
            <div style={{ color: '#445468', lineHeight: 1.7 }}>
              Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br />Phường Xuân Phương, TP Hà Nội
            </div>
          </div>
        </div>
        <div style={{ borderTop: '1px solid #EEF1F5', marginTop: 26, paddingTop: 18, color: '#718096', fontSize: 13, textAlign: 'center' }}>
          © FPT Polytechnic. All rights reserved.
        </div>
      </div>
      <style>{`@media(max-width:767px){.fpt-footer-grid{grid-template-columns:1fr !important;gap:22px !important;}}`}</style>
    </footer>
  );
};

const FptLearnerBanner = () => (
  <div style={{
    position: 'relative',
    overflow: 'hidden',
    minHeight: 150,
    border: '1px solid #E6ECF5',
    borderRadius: 14,
    marginBottom: 24,
    boxShadow: '0 8px 24px rgba(11,59,130,.05)',
    background: `linear-gradient(90deg, rgba(255,255,255,.98) 0%, rgba(255,247,237,.94) 58%, rgba(255,247,237,.18) 100%), url(${getFptAsset('fpt-hanoi-campus.jpg')}) center 43% / cover no-repeat`,
  }}>
    <div style={{ maxWidth: 650, padding: '28px 30px', position: 'relative', zIndex: 1 }}>
      <div style={{ color: '#0B3B82', fontSize: 24, fontWeight: 800, marginBottom: 6 }}>Tiếp tục hành trình học tập</div>
      <div style={{ color: '#58677A', lineHeight: 1.55 }}>Chọn một khóa học bên dưới để tiếp tục học tập trên FPT Polytechnic.</div>
    </div>
  </div>
);
""",
))


FPT_FOOTER_SLOT = (
    "org.openedx.frontend.layout.footer.v1",
    r"""
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'indigo_footer' },
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'default_contents' },
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_footer',
        type: DIRECT_PLUGIN,
        priority: 100,
        RenderWidget: FptFooter,
      },
    },
""",
)

FPT_LOGO_SLOT = (
    "logo_slot",
    r"""
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'custom_logo' },
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'default_contents' },
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_logo',
        type: DIRECT_PLUGIN,
        priority: 100,
        RenderWidget: FptHeaderLogo,
      },
    },
""",
)

FPT_HIDE_THEME_TOGGLE = (
    "desktop_secondary_menu_slot",
    r"""{ op: PLUGIN_OPERATIONS.Hide, widgetId: 'theme_switch_button' },""",
)

for _mfe in ["learning", "learner-dashboard", "profile", "account", "discussions", "authoring", "authn"]:
    PLUGIN_SLOTS.add_item((_mfe, *FPT_FOOTER_SLOT))
    PLUGIN_SLOTS.add_item((_mfe, *FPT_HIDE_THEME_TOGGLE))

# Insert only; default Indigo CourseList remains intact.
PLUGIN_SLOTS.add_item((
    "learner-dashboard",
    "org.openedx.frontend.learner_dashboard.course_list.v1",
    r"""
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_learner_banner',
        type: DIRECT_PLUGIN,
        priority: 1,
        RenderWidget: FptLearnerBanner,
      },
    },
""",
))


@MFE_APPS.add()
def _fpt_brand_all_mfes(mfes: dict[str, MFE_ATTRS_TYPE]) -> dict[str, MFE_ATTRS_TYPE]:
    for mfe in mfes:
        PLUGIN_SLOTS.add_item((str(mfe), *FPT_LOGO_SLOT))
    return mfes


# -----------------------------------------------------------------------------
# Open edX/Indigo legacy LMS assets + Course Discovery hero + footer.
# The external assets are downloaded ONCE while building the openedx image and
# then served locally from /static/indigo/images/fpt/. No production hotlinking.
# -----------------------------------------------------------------------------
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    f"""
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
<div class="fpt-slide is-active">
  <div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-students.jpg" alt="Sinh viên FPT Polytechnic"></div>
  <div class="fpt-slide__content"><h1>Học tập cùng FPT Polytechnic</h1><p>Nâng cao kiến thức, phát triển kỹ năng và tiếp tục hành trình học tập trên nền tảng số.</p><a href="#discovery-form">Khám phá khóa học</a></div>
</div>
<div class="fpt-slide">
  <div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-hanoi-campus.jpg" alt="FPT Polytechnic Hà Nội"></div>
  <div class="fpt-slide__content"><h1>Học mọi lúc, mọi nơi</h1><p>Truy cập khóa học, nội dung và hoạt động học tập thuận tiện trên nhiều thiết bị.</p><a href="#discovery-form">Tìm khóa học</a></div>
</div>
<div class="fpt-slide">
  <div class="fpt-slide__photo"><img src="/static/indigo/images/fpt/fpt-campus.jpg" alt="Không gian FPT Polytechnic"></div>
  <div class="fpt-slide__content"><h1>Sẵn sàng cho tương lai</h1><p>Môi trường học tập hiện đại, tập trung vào trải nghiệm sinh viên và năng lực nghề nghiệp.</p><a href="#discovery-form">Bắt đầu ngay</a></div>
</div>
<div class="fpt-hero__nav">
  <button class="fpt-dot is-active" type="button" aria-label="Slide 1"></button>
  <button class="fpt-dot" type="button" aria-label="Slide 2"></button>
  <button class="fpt-dot" type="button" aria-label="Slide 3"></button>
</div>
<script>
(function(){{
  var root=document.getElementById('fpt-hero-slider'); if(!root)return;
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
""",
))
