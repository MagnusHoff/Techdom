import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Techdom Boliganalyse",
  description: "Neste generasjons SaaS-grensesnitt for Techdom.ai",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="no">
      <body>{children}</body>
    </html>
  );
}
