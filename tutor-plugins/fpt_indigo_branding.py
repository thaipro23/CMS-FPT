from __future__ import annotations

from tutor import hooks
from tutormfe.hooks import MFE_APPS, MFE_ATTRS_TYPE, PLUGIN_SLOTS


# -----------------------------------------------------------------------------
# FPT Polytechnic branding layer for Tutor Indigo (Ulmo / Tutor 21)
#
# Goals:
# - Keep Indigo/Paragon layouts and application logic.
# - Disable dark mode and public registration.
# - Replace Indigo header logo/footer with FPT branding through plugin slots.
# - Add a small Learner Dashboard banner without replacing the course list.
# - Add a lightweight hero slider above the existing Indigo Course Discovery
#   search/filter/grid.
# - Keep FEID / third-party authentication untouched.
# -----------------------------------------------------------------------------

FPT_PRIMARY = "#0B3B82"
FPT_PRIMARY_DARK = "#072B61"
FPT_ACCENT = "#F97316"


# Runtime config used by Authn MFE and Indigo components.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-common-settings",
    f'''
MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False
MFE_CONFIG["INDIGO_FOOTER_NAV_LINKS"] = []
MFE_CONFIG["ALLOW_PUBLIC_ACCOUNT_CREATION"] = False
MFE_CONFIG["SHOW_REGISTRATION_LINKS"] = False
MFE_CONFIG["SITE_NAME"] = "FPT Polytechnic"
MFE_CONFIG["FPT_PRIMARY_COLOR"] = "{FPT_PRIMARY}"
MFE_CONFIG["FPT_ACCENT_COLOR"] = "{FPT_ACCENT}"
''',
))


# Authn: keep upstream login form/FEID logic, only adjust branding text/CSS.
# The login MFE already supports hiding Register via the config above.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-post-npm-install-authn",
    r'''
RUN python - <<'PY'
from pathlib import Path

messages = Path('/openedx/app/src/base-container/components/default-layout/messages.js')
if messages.exists():
    text = messages.read_text(encoding='utf-8')
    text = text.replace("defaultMessage: 'Start learning'", "defaultMessage: 'Học tập cùng'")
    text = text.replace("defaultMessage: 'with {siteName}'", "defaultMessage: '{siteName}'")
    messages.write_text(text, encoding='utf-8')

scss_candidates = [
    Path('/openedx/app/src/index.scss'),
    Path('/openedx/app/src/index.css'),
]
branding = r'''
/* FPT Polytechnic branding overlay - preserves Authn MFE structure/logic. */
:root {
  --fpt-primary: #0B3B82;
  --fpt-primary-dark: #072B61;
  --fpt-accent: #F97316;
}

.btn-primary,
.btn-primary:not(:disabled):not(.disabled):active,
.btn-primary:not(:disabled):not(.disabled).active {
  background: var(--fpt-primary) !important;
  border-color: var(--fpt-primary) !important;
}
.btn-primary:hover,
.btn-primary:focus {
  background: var(--fpt-primary-dark) !important;
  border-color: var(--fpt-primary-dark) !important;
}
.text-accent-a { color: var(--fpt-accent) !important; }
.bg-primary-400, .bg-primary-500 { background-color: var(--fpt-primary) !important; }

/* Keep the native Indigo/Authn two-column layout; enhance only the left hero. */
.layout > .w-50.d-flex > .col-md-9.bg-primary-400,
.layout .banner__image.large-layout {
  position: relative;
  overflow: hidden;
  background-color: var(--fpt-primary) !important;
  background-image:
    radial-gradient(circle at 18% 80%, rgba(65, 166, 255, .30), transparent 34%),
    radial-gradient(circle at 85% 18%, rgba(249, 115, 22, .24), transparent 28%),
    linear-gradient(145deg, #0B3B82 0%, #0A56A4 58%, #07326D 100%) !important;
}
.layout > .w-50.d-flex > .col-md-9.bg-primary-400::after,
.layout .banner__image.large-layout::after {
  content: '';
  position: absolute;
  inset: auto -70px -110px 32%;
  height: 420px;
  border: 2px solid rgba(255,255,255,.10);
  border-radius: 48% 52% 0 0;
  transform: rotate(-8deg);
  pointer-events: none;
}
.layout h1.display-2 {
  position: relative;
  z-index: 2;
  max-width: 620px !important;
  letter-spacing: -0.03em;
}
.layout .content {
  background: #F7F9FC;
}
.layout .content > div {
  width: min(100%, 620px);
}
#main-content {
  background: #fff;
  border: 1px solid #E7EBF2;
  border-radius: 18px;
  box-shadow: 0 18px 48px rgba(11, 59, 130, .08);
  padding: 32px;
}
@media (max-width: 767.98px) {
  #main-content { padding: 22px; border-radius: 14px; }
}
'''
for scss in scss_candidates:
    if scss.exists():
        scss.write_text(scss.read_text(encoding='utf-8') + '\n' + branding, encoding='utf-8')
        break
PY
''',
))


# Runtime React components injected through the same mechanism used by Indigo.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-runtime-definitions",
    r'''
const FptInlineLogo = ({ compact = false }) => (
  <svg
    viewBox="0 0 330 62"
    role="img"
    aria-label="FPT Polytechnic"
    style={{ height: compact ? 30 : 38, width: compact ? 160 : 205, display: 'block' }}
  >
    <g transform="translate(2 7) skewX(-12)">
      <rect width="46" height="46" rx="5" fill="#1677C8" />
      <rect x="36" width="46" height="46" rx="5" fill="#F97316" />
      <rect x="72" width="46" height="46" rx="5" fill="#32A852" />
    </g>
    <g fill="#fff" fontFamily="Arial, sans-serif" fontWeight="800" fontSize="26">
      <text x="13" y="40">F</text><text x="50" y="40">P</text><text x="87" y="40">T</text>
    </g>
    <text x="133" y="39" fill="#F36F21" fontFamily="Arial, sans-serif" fontWeight="800" fontSize="21">FPT POLYTECHNIC</text>
  </svg>
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
  React.useEffect(() => {
    const names = ['selected-paragon-theme-variant', 'selected-theme-variant'];
    names.forEach(name => window.localStorage.setItem(name, 'light'));
    document.documentElement.setAttribute('data-paragon-theme-variant', 'light');
    document.documentElement.setAttribute('data-theme-variant', 'light');
  }, []);

  return (
    <footer style={{ borderTop: '1px solid #E7EBF2', background: '#fff', marginTop: 40 }}>
      <div className="container-fluid" style={{ maxWidth: 1320, padding: '34px 24px 26px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.4fr)', gap: 32, alignItems: 'start' }}>
          <div><FptInlineLogo /></div>
          <div>
            <div style={{ color: '#0B3B82', fontWeight: 800, marginBottom: 10 }}>THÔNG TIN LIÊN HỆ</div>
            <a href="mailto:caodang@fpt.edu.vn" style={{ color: '#26364A', textDecoration: 'none' }}>caodang@fpt.edu.vn</a>
          </div>
          <div>
            <div style={{ color: '#0B3B82', fontWeight: 800, marginBottom: 10 }}>Trụ sở chính</div>
            <div style={{ color: '#445468', lineHeight: 1.7 }}>Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br />Phường Xuân Phương, TP Hà Nội</div>
          </div>
        </div>
        <div style={{ borderTop: '1px solid #EEF1F5', marginTop: 26, paddingTop: 18, color: '#718096', fontSize: 13, textAlign: 'center' }}>
          © FPT Polytechnic. All rights reserved.
        </div>
      </div>
    </footer>
  );
};

const FptLearnerBanner = () => (
  <div style={{
    background: 'linear-gradient(100deg, #F7FAFF 0%, #FFF7ED 100%)',
    border: '1px solid #E6ECF5',
    borderRadius: 14,
    padding: '22px 26px',
    marginBottom: 24,
    boxShadow: '0 8px 24px rgba(11,59,130,.04)',
  }}>
    <div style={{ color: '#0B3B82', fontSize: 22, fontWeight: 800, marginBottom: 4 }}>Tiếp tục hành trình học tập</div>
    <div style={{ color: '#58677A' }}>Các khóa học của bạn vẫn giữ nguyên trải nghiệm Indigo; hãy chọn một khóa học để tiếp tục.</div>
  </div>
);
''',
))


# Replace Indigo footer/logo widgets but keep Indigo layout and all application logic.
FPT_FOOTER_SLOT = (
    "org.openedx.frontend.layout.footer.v1",
    r'''
    {
      op: PLUGIN_OPERATIONS.Hide,
      widgetId: 'indigo_footer',
    },
    {
      op: PLUGIN_OPERATIONS.Hide,
      widgetId: 'default_contents',
    },
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_footer',
        type: DIRECT_PLUGIN,
        priority: 100,
        RenderWidget: FptFooter,
      },
    },
''',
)

FPT_LOGO_SLOT = (
    "logo_slot",
    r'''
    {
      op: PLUGIN_OPERATIONS.Hide,
      widgetId: 'custom_logo',
    },
    {
      op: PLUGIN_OPERATIONS.Hide,
      widgetId: 'default_contents',
    },
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_logo',
        type: DIRECT_PLUGIN,
        priority: 100,
        RenderWidget: FptHeaderLogo,
      },
    },
''',
)

# Explicitly hide Indigo theme switch wherever its slot is present.
FPT_HIDE_THEME_TOGGLE = (
    "desktop_secondary_menu_slot",
    r'''
    {
      op: PLUGIN_OPERATIONS.Hide,
      widgetId: 'theme_switch_button',
    },
''',
)


# Main MFEs in this installation.
for _mfe in [
    "learning",
    "learner-dashboard",
    "profile",
    "account",
    "discussions",
    "authoring",
    "authn",
]:
    PLUGIN_SLOTS.add_item((_mfe, *FPT_FOOTER_SLOT))
    PLUGIN_SLOTS.add_item((_mfe, *FPT_HIDE_THEME_TOGGLE))

# Add a banner while preserving Learner Dashboard's default CourseList.
PLUGIN_SLOTS.add_item((
    "learner-dashboard",
    "org.openedx.frontend.learner_dashboard.course_list.v1",
    r'''
    {
      op: PLUGIN_OPERATIONS.Insert,
      widget: {
        id: 'fpt_learner_banner',
        type: DIRECT_PLUGIN,
        priority: 1,
        RenderWidget: FptLearnerBanner,
      },
    },
''',
))


@MFE_APPS.add()
def _fpt_brand_all_mfes(mfes: dict[str, MFE_ATTRS_TYPE]) -> dict[str, MFE_ATTRS_TYPE]:
    for mfe in mfes:
        PLUGIN_SLOTS.add_item((str(mfe), *FPT_LOGO_SLOT))
    return mfes


# Legacy LMS Course Discovery + footer are part of the comprehensive Indigo theme.
# We patch only rendered Indigo templates AFTER assets are collected, so no edx-platform
# core file is changed and Search/Filters/Course Grid remain untouched.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    r'''
RUN python - <<'PY'
from pathlib import Path

courses = Path('/openedx/themes/indigo/lms/templates/courseware/courses.html')
if courses.exists():
    text = courses.read_text(encoding='utf-8')
    if 'id="fpt-hero-slider"' not in text:
        anchor = '<section class="courses-container">'
        hero = r'''
<section id="fpt-hero-slider" class="fpt-hero" aria-label="FPT Polytechnic">
  <style>
    .fpt-hero{position:relative;overflow:hidden;margin:0 0 28px;border-radius:0;background:#0B3B82;color:#fff;min-height:310px;box-shadow:0 14px 34px rgba(11,59,130,.12)}
    .fpt-slide{display:none;min-height:310px;padding:54px 7%;align-items:center;background:radial-gradient(circle at 82% 26%,rgba(255,255,255,.16),transparent 26%),linear-gradient(120deg,#0B3B82,#0A5AA8)}
    .fpt-slide.is-active{display:flex}.fpt-slide__content{max-width:640px;position:relative;z-index:2}.fpt-slide h1{margin:0 0 14px;color:#fff;font-size:42px;line-height:1.12;font-weight:800;letter-spacing:-.025em}.fpt-slide p{margin:0 0 24px;max-width:560px;font-size:18px;line-height:1.55;color:rgba(255,255,255,.9)}
    .fpt-slide a{display:inline-block;background:#F97316;color:#fff!important;padding:12px 20px;border-radius:7px;font-weight:700;text-decoration:none}.fpt-slide a:hover{background:#E9640C}
    .fpt-shape{position:absolute;right:8%;top:50%;width:310px;height:210px;transform:translateY(-50%) rotate(-4deg);border-radius:28px;background:linear-gradient(145deg,rgba(255,255,255,.96),rgba(255,255,255,.76));box-shadow:0 24px 55px rgba(0,0,0,.18)}
    .fpt-shape:before,.fpt-shape:after{content:'';position:absolute;border-radius:18px}.fpt-shape:before{left:28px;top:30px;width:112px;height:145px;background:linear-gradient(160deg,#EEF6FF,#B9D9F8)}.fpt-shape:after{right:25px;bottom:26px;width:120px;height:92px;background:linear-gradient(145deg,#FFF1E6,#F97316)}
    .fpt-hero__nav{position:absolute;z-index:4;left:50%;bottom:18px;transform:translateX(-50%);display:flex;gap:8px}.fpt-dot{width:9px;height:9px;border:0;border-radius:50%;background:rgba(255,255,255,.45);padding:0}.fpt-dot.is-active{width:24px;border-radius:8px;background:#fff}
    @media(max-width:900px){.fpt-shape{opacity:.28;right:-80px}.fpt-slide h1{font-size:34px}}@media(max-width:600px){.fpt-hero,.fpt-slide{min-height:280px}.fpt-slide{padding:38px 24px}.fpt-slide h1{font-size:30px}.fpt-slide p{font-size:16px}.fpt-shape{display:none}}
  </style>
  <div class="fpt-slide is-active">
    <div class="fpt-slide__content"><h1>Học tập cùng FPT Polytechnic</h1><p>Nâng cao kiến thức, phát triển kỹ năng và tiếp tục hành trình học tập trên nền tảng số.</p><a href="#discovery-form">Khám phá khóa học</a></div><div class="fpt-shape" aria-hidden="true"></div>
  </div>
  <div class="fpt-slide">
    <div class="fpt-slide__content"><h1>Học mọi lúc, mọi nơi</h1><p>Truy cập khóa học, nội dung và hoạt động học tập thuận tiện trên nhiều thiết bị.</p><a href="#discovery-form">Tìm khóa học</a></div><div class="fpt-shape" aria-hidden="true"></div>
  </div>
  <div class="fpt-slide">
    <div class="fpt-slide__content"><h1>Sẵn sàng cho tương lai</h1><p>Môi trường học tập hiện đại, tập trung vào trải nghiệm sinh viên và năng lực nghề nghiệp.</p><a href="#discovery-form">Bắt đầu ngay</a></div><div class="fpt-shape" aria-hidden="true"></div>
  </div>
  <div class="fpt-hero__nav" aria-label="Chuyển slide"><button class="fpt-dot is-active" type="button" aria-label="Slide 1"></button><button class="fpt-dot" type="button" aria-label="Slide 2"></button><button class="fpt-dot" type="button" aria-label="Slide 3"></button></div>
  <script>
    (function(){var root=document.getElementById('fpt-hero-slider');if(!root)return;var slides=root.querySelectorAll('.fpt-slide'),dots=root.querySelectorAll('.fpt-dot'),i=0,timer;function show(n){i=(n+slides.length)%slides.length;slides.forEach(function(s,x){s.classList.toggle('is-active',x===i)});dots.forEach(function(d,x){d.classList.toggle('is-active',x===i)})}function start(){timer=setInterval(function(){show(i+1)},6500)}dots.forEach(function(d,x){d.addEventListener('click',function(){clearInterval(timer);show(x);start()})});root.addEventListener('mouseenter',function(){clearInterval(timer)});root.addEventListener('mouseleave',start);start()})();
  </script>
</section>
'''
        if anchor in text:
            text = text.replace(anchor, anchor + hero, 1)
            courses.write_text(text, encoding='utf-8')

footer = Path('/openedx/themes/indigo/lms/templates/footer.html')
if footer.exists():
    footer.write_text(r'''
<footer class="fpt-legacy-footer" style="border-top:1px solid #E7EBF2;background:#fff;margin-top:40px">
  <div style="max-width:1320px;margin:0 auto;padding:34px 24px 26px;display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.4fr);gap:32px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;color:#F36F21;font-weight:800;font-size:18px"><span style="display:inline-flex;gap:2px"><b style="background:#1677C8;color:#fff;padding:7px 8px;border-radius:4px">F</b><b style="background:#F97316;color:#fff;padding:7px 8px;border-radius:4px">P</b><b style="background:#32A852;color:#fff;padding:7px 8px;border-radius:4px">T</b></span> FPT POLYTECHNIC</div>
    </div>
    <div><strong style="color:#0B3B82">THÔNG TIN LIÊN HỆ</strong><p style="margin:10px 0 0"><a href="mailto:caodang@fpt.edu.vn" style="color:#26364A">caodang@fpt.edu.vn</a></p></div>
    <div><strong style="color:#0B3B82">Trụ sở chính</strong><p style="margin:10px 0 0;color:#445468;line-height:1.7">Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br/>Phường Xuân Phương, TP Hà Nội</p></div>
  </div>
  <div style="max-width:1320px;margin:0 auto;border-top:1px solid #EEF1F5;padding:18px 24px 24px;text-align:center;color:#718096;font-size:13px">© FPT Polytechnic. All rights reserved.</div>
</footer>
''', encoding='utf-8')
PY
''',
))
