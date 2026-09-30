import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { AuthProvider } from "@/contexts/AuthContext";
import PrivacyDefaults from "@/components/PrivacyDefaults";

// Self-hosted Inter (variable, latin subset) — no requests to Google. The
// woff2 is vendored in the repo so builds are hermetic and visitor IPs are
// never exposed to a third-party font CDN.
const inter = localFont({
  src: "../fonts/inter-latin-wght-normal.woff2",
  weight: "100 900",
  style: "normal",
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ContractOS",
  description: "Contract Lifecycle Management Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.variable} font-sans antialiased`}>
        <PrivacyDefaults />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
