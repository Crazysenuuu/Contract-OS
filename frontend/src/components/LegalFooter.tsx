import Link from "next/link";

const LINKS = [
  { href: "/legal/terms", label: "Terms" },
  { href: "/legal/privacy", label: "Privacy" },
  { href: "/legal/cookies", label: "Cookies" },
  { href: "/legal/dmca", label: "Copyright / DMCA" },
] as const;

/** Shared footer of public policy links for the unauthenticated pages. */
export default function LegalFooter() {
  return (
    <nav
      aria-label="Legal policies"
      className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-gray-400"
    >
      {LINKS.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className="hover:text-gray-500"
        >
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
