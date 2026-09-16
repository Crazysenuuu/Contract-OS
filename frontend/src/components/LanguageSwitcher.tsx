"use client";

import { useEffect, useState, useCallback, createContext, useContext, ReactNode } from "react";

// RTL languages (module scope: stable identity, no effect-deps churn)
const RTL_LANGUAGES = ["ar", "he", "fa", "ur"];

// Language context
interface LanguageContextType {
  language: string;
  setLanguage: (code: string) => void;
  t: (key: string) => string;
  dir: "ltr" | "rtl";
}

const LanguageContext = createContext<LanguageContextType>({
  language: "en",
  setLanguage: () => {},
  t: (key: string) => key,
  dir: "ltr",
});

export const useLanguage = () => useContext(LanguageContext);

// Provider
export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState("en");
  const [translations, setTranslations] = useState<Record<string, string>>({});

  // Text direction is derived from the language — no state, no sync effect.
  const dir: "ltr" | "rtl" = RTL_LANGUAGES.includes(language) ? "rtl" : "ltr";

  const loadTranslations = useCallback(async (langCode: string) => {
    try {
      const response = await fetch(`/api/v1/i18n/translations/ui?language=${langCode}`);
      if (response.ok) {
        const data = await response.json();
        setTranslations(data.translations || {});
      }
    } catch (err) {
      console.error("Failed to load translations:", err);
    }
  }, []);

  // Hydrate the saved language preference after mount (localStorage is
  // unavailable during SSR). The setState is deferred to a microtask so it
  // never fires synchronously within the effect.
  useEffect(() => {
    const saved = localStorage.getItem("contractos_language");
    if (saved && saved !== "en") {
      queueMicrotask(() => setLanguageState(saved));
    }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) loadTranslations(language);
    });
    // Persist the preference (external system write — allowed in effects).
    localStorage.setItem("contractos_language", language);
    return () => {
      active = false;
    };
  }, [language, loadTranslations]);

  const setLanguage = (code: string) => {
    setLanguageState(code);
  };

  const t = (key: string): string => {
    return translations[key] || key;
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t, dir }}>
      <div dir={dir}>
        {children}
      </div>
    </LanguageContext.Provider>
  );
}

// Language Switcher Component
interface Language {
  code: string;
  name: string;
  native_name: string;
  direction: string;
}

export function LanguageSwitcher() {
  const { language, setLanguage } = useLanguage();
  const [languages, setLanguages] = useState<Language[]>([]);
  const [isOpen, setIsOpen] = useState(false);

  const loadLanguages = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/i18n/languages");
      if (response.ok) {
        const data = await response.json();
        setLanguages(data);
      }
    } catch {
      // Use defaults if API not available
      setLanguages([
        { code: "en", name: "English", native_name: "English", direction: "ltr" },
        { code: "si", name: "Sinhala", native_name: "සිංහල", direction: "ltr" },
        { code: "ta", name: "Tamil", native_name: "தமிழ்", direction: "ltr" },
        { code: "zh", name: "Chinese", native_name: "中文", direction: "ltr" },
        { code: "ar", name: "Arabic", native_name: "العربية", direction: "rtl" },
        { code: "hi", name: "Hindi", native_name: "हिन्दी", direction: "ltr" },
        { code: "ja", name: "Japanese", native_name: "日本語", direction: "ltr" },
        { code: "de", name: "German", native_name: "Deutsch", direction: "ltr" },
      ]);
    }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) loadLanguages();
    });
    return () => {
      active = false;
    };
  }, [loadLanguages]);

  const currentLang = languages.find((l) => l.code === language);

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded border hover:bg-gray-50 text-sm"
      >
        <span>🌐</span>
        <span>{currentLang?.native_name || language}</span>
        <svg
          className={`w-4 h-4 transition-transform ${isOpen ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-lg border z-50">
            <div className="py-1">
              {languages.map((lang) => (
                <button
                  key={lang.code}
                  onClick={() => {
                    setLanguage(lang.code);
                    setIsOpen(false);
                  }}
                  className={`w-full px-4 py-2 text-left hover:bg-gray-100 flex items-center justify-between ${
                    language === lang.code ? "bg-blue-50 text-blue-700" : ""
                  }`}
                >
                  <span>{lang.name}</span>
                  <span className="text-sm text-gray-500">{lang.native_name}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Translation component
export function T({ k }: { k: string }) {
  const { t } = useLanguage();
  return <>{t(k)}</>;
}
