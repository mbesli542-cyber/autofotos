import type { MetadataRoute } from "next";
import { APP_INFO, BRAND } from "@/config/brand";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: APP_INFO.name,
    short_name: APP_INFO.shortName,
    description: APP_INFO.description,
    lang: "de",
    dir: "ltr",
    start_url: "/fahrzeuge",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: BRAND.colors.background,
    theme_color: BRAND.colors.background,
    categories: ["business", "productivity", "photo"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
