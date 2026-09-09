const SVG = {
  search: '<svg class="ic" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
  menu: '<svg class="ic" viewBox="0 0 24 24"><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/></svg>',
  phone: '<svg class="ic" viewBox="0 0 24 24"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  mail: '<svg class="ic" viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>',
  pin: '<svg class="ic" viewBox="0 0 24 24"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>',
  heart: '<svg class="ic" viewBox="0 0 24 24"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>',
  instagram: '<svg class="ic" viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" x2="17.51" y1="6.5" y2="6.5"/></svg>',
  facebook: '<svg class="ic" viewBox="0 0 24 24"><path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/></svg>',
  x: '<svg class="ic" viewBox="0 0 24 24"><path d="M4 4l16 16M20 4 4 20"/></svg>',
  youtube: '<svg class="ic" viewBox="0 0 24 24"><path d="M2.5 17a24.12 24.12 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.56 49.56 0 0 1 16.2 0A2 2 0 0 1 21.5 7a24.12 24.12 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.55 49.55 0 0 1-16.2 0A2 2 0 0 1 2.5 17"/><path d="m10 15 5-3-5-3z"/></svg>',
  linkedin: '<svg class="ic" viewBox="0 0 24 24"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/></svg>',
};

const NAV_LINKS = [
  { key: "home", href: "index.html", label: "Home" },
  { key: "cars", href: "cars.html", label: "Cars" },
  { key: "bookings", href: "my-bookings.html", label: "My Bookings" },
  { key: "admin", href: "admin.html", label: "Admin" },
];


function Navbar(active) {
  const links = NAV_LINKS.map((l) =>
    `<a href="${l.href}"${l.key === active ? ' class="active"' : ""}>${l.label}</a>`).join("");
  return `
<header class="nav">
  <div class="container nav-inner">
    <a href="index.html" class="logo-wrap"><span class="logo">DriveGo<span class="logo-dot">.</span></span><span class="nav-tag">Rent. Drive. Repeat.</span></a>
    <nav class="nav-links" id="navLinks">${links}</nav>
    <div class="nav-actions">
      <a href="cars.html" class="nav-search" aria-label="Search">${SVG.search}</a>
      <a href="login.html" class="btn btn-ghost">Login</a>
      <a href="cars.html" class="btn btn-primary">Book a Car</a>
      <button class="nav-toggle" id="navToggle" aria-label="Menu">${SVG.menu}</button>
    </div>
  </div>
</header>`;
}


function Footer(variant) {
  const note = variant === "light"
    ? `<span>Made with ${SVG.heart} in India</span>`
    : `<span>Mock data only · No backend connected</span>`;
  const rights = variant === "light"
    ? `© 2026 DriveGo. All rights reserved.`
    : `© 2026 DriveGo (frontend prototype)`;
  return `
<footer class="site-footer">
  <div class="container sf-grid">
    <div class="sf-brand">
      <div class="logo">DriveGo<span class="logo-dot">.</span></div>
      <p class="sf-tag">Rent. Drive. Repeat.</p>
      <p class="sf-desc">Verified cars from 5 locations across Kolkata. Flexible booking, transparent pricing, secure checkout.</p>
      <div class="sf-social"><a href="index.html" aria-label="Instagram">${SVG.instagram}</a><a href="index.html" aria-label="Facebook">${SVG.facebook}</a><a href="index.html" aria-label="X">${SVG.x}</a><a href="index.html" aria-label="YouTube">${SVG.youtube}</a><a href="index.html" aria-label="LinkedIn">${SVG.linkedin}</a></div>
    </div>
    <div><h4>Company</h4><a href="index.html">About</a><a href="index.html">Contact Us</a><a href="index.html">Help Center</a></div>
    <div><h4>Explore</h4><a href="cars.html">Cars</a><a href="index.html#locations">Locations</a><a href="index.html#how">How It Works</a><a href="my-bookings.html">My Bookings</a></div>
    <div><h4>Support</h4><a href="index.html">Terms &amp; Conditions</a><a href="index.html">Privacy Policy</a><a href="login.html">Login</a></div>
    <div class="footer-contact"><h4>Contact</h4><p class="small">${SVG.phone}+91 98765 43210</p><p class="small">${SVG.mail}support@drivego.com</p><p class="small">${SVG.pin}Kolkata, West Bengal</p></div>
  </div>
  <div class="container sf-bottom"><span>${rights}</span><span class="sf-legal"><a href="index.html">Terms</a><a href="index.html">Privacy</a></span>${note}</div>
</footer>`;
}


document.addEventListener("DOMContentLoaded", () => {
  const navSlot = document.getElementById("siteNav");
  if (navSlot) navSlot.innerHTML = Navbar(document.body.dataset.nav || "");
  const footSlot = document.getElementById("siteFoot");
  if (footSlot) footSlot.innerHTML = Footer(document.body.dataset.footer || "dark");
});
