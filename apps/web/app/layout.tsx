import "@fontsource-variable/manrope";
import "@fontsource/ibm-plex-mono/500.css";
import "./globals.css";

import { MotionProvider } from "@ecommerce-agent-system/ui";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Relay Desk",
  description: "E-commerce service agent workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
