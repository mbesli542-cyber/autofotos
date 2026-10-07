import type { Metadata, Viewport } from "next";
import { AppProviders } from "@/components/providers/AppProviders";
import { APP_INFO, BRAND } from "@/config/brand";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: APP_INFO.name,
    template: `%s · ${APP_INFO.shortName}`,
  },
  description: APP_INFO.description,
  applicationName: APP_INFO.name,
  appleWebApp: {
    capable: true,
    title: APP_INFO.shortName,
    statusBarStyle: "black-translucent",
  },
  formatDetection: { telephone: false },
  icons: {
    // Official AutoExperten "AE" monogram (public/brand/official/AutoExperten_Icon.png).
    icon: [
      { url: "/icons/favicon-48.png", sizes: "48x48", type: "image/png" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }],
  },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: BRAND.colors.background,
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="de" className="h-full antialiased">
      <body className="min-h-full">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
