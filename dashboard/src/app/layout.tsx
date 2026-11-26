import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

/**
 * IBM Plex, not Inter.
 *
 * Plex was drawn for technical and scientific documentation, and its slightly
 * mechanical letterforms read as instrumentation rather than as another SaaS
 * landing page. Sans carries the interface and all quantities; Mono is used
 * only for code-like tokens — pod names, run ids, timestamps — never for
 * display figures.
 */
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CloudPulse — control plane",
  description:
    "Load-aware predictive auto-scaling for Kubernetes. Conformal forecasts, hybrid arbitration, live telemetry.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Resolve the theme before first paint, so there is no flash.
            Sand is the default; the OS preference does not override it, because
            the warm ground is the designed state rather than a light variant. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('cp-theme')||'light';document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`,
          }}
        />
      </head>
      <body className={`${plexSans.variable} ${plexMono.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
