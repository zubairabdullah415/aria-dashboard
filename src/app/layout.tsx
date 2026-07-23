import type { Metadata, Viewport } from "next";
import { Inter, Noto_Nastaliq_Urdu } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

// Noto Nastaliq Urdu — covers Urdu script (Nastaliq) for bilingual chat.
// Loaded as a CSS variable so it can sit in the font stack as a fallback;
// the browser automatically uses it for Urdu codepoints and Inter for Latin.
const notoNastaliq = Noto_Nastaliq_Urdu({
  variable: "--font-nastaliq",
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Aria — AI Reservation Concierge",
  description:
    "Chat with Aria to book your table effortlessly. Powered by LiftUp AI.",
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#064e3b",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${notoNastaliq.variable} antialiased`}
      >
        {children}
        <Toaster richColors position="top-center" />
      </body>
    </html>
  );
}
