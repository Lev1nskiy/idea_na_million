import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Inter, JetBrains_Mono, Unbounded } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { BackendNotice } from "@/components/BackendNotice";

// Шрифты с кириллическими подмножествами
const unbounded = Unbounded({ subsets: ["latin", "cyrillic"], variable: "--font-unbounded", weight: ["400", "500", "600", "700"] });
const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-inter" });
const jbmono = JetBrains_Mono({ subsets: ["latin", "cyrillic"], variable: "--font-jbmono" });

export const metadata: Metadata = {
  title: "ЛОСЬ — AI-native CRM",
  description: "Демо-фронтенд AI-native CRM: чат клиента, дашборд менеджера, Policy Management, безопасность и метрики.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru" className={`${unbounded.variable} ${inter.variable} ${jbmono.variable}`}>
      <body className="bg-ink text-txt antialiased">
        {/*
          md:flex — на широком экране сайдбар и контент стоят рядом, на узком компактная
          шапка с меню (Nav) встаёт над страницей и ничего не перекрывает.
        */}
        <div className="min-h-screen md:flex">
          <Nav />
          <main className="min-w-0 flex-1 px-5 pb-10 pt-5 md:px-8">
            {/* Сайт умеет работать на демо-данных — но об этом надо сказать вслух, иначе
                «карточка из бота не дошла» выглядит как поломка бота, а не как mock-режим. */}
            <BackendNotice />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
