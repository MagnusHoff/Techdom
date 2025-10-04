"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

interface SiteHeaderProps {
  showAction?: boolean;
  actionHref?: string;
  actionLabel?: string;
}

export function SiteHeader({
  showAction = false,
  actionHref = "/",
  actionLabel = "Ny analyse",
}: SiteHeaderProps) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 16);
    };
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const headerClass = scrolled ? "site-header is-scrolled" : "site-header";

  return (
    <header className={headerClass}>
      <Link href="/" className="brand-pill">
        Techdom.AI – eiendomsanalyse
      </Link>
      {showAction ? (
        <Link href={actionHref} className="header-action">
          {actionLabel}
        </Link>
      ) : null}
    </header>
  );
}

type PageContainerVariant = "default" | "narrow";

interface PageContainerProps {
  children: React.ReactNode;
  variant?: PageContainerVariant;
}

export function PageContainer({ children, variant = "default" }: PageContainerProps) {
  const classes = ["page-shell"];
  if (variant === "narrow") {
    classes.push("page-shell--narrow");
  }
  return <div className={classes.join(" ")}>{children}</div>;
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-links">
        <a href="https://instagram.com/techdom.ai" target="_blank" rel="noreferrer">
          Instagram: techdom.ai
        </a>
        <span className="footer-separator">·</span>
        <a href="mailto:techdom.ai@techdom.com">Mail: techdom.ai@techdom.com</a>
      </div>
      <p>
        Techdom.ai tilbyr kun generell og veiledende informasjon. Vi garanterer ikke at analysene er
        fullstendige, korrekte eller oppdaterte, og vi fraskriver oss ethvert ansvar for tap eller beslutninger
        basert på informasjon fra plattformen. All bruk skjer på eget ansvar, og vi anbefaler å søke profesjonell
        rådgivning før du tar investeringsbeslutninger.
      </p>
    </footer>
  );
}
