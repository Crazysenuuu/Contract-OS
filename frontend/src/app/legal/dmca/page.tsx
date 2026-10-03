import type { Metadata } from "next";
import Link from "next/link";
import { LEGAL_CONTACTS } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Copyright / DMCA Policy — ContractOS",
  description:
    "How to submit a Digital Millennium Copyright Act (DMCA) takedown notice to ContractOS.",
};

const CONTACT_EMAIL = LEGAL_CONTACTS.dmca;

/**
 * Copyright / DMCA policy page.
 *
 * Publishing a reachable policy with designated-agent contact details is a
 * prerequisite for a DMCA safe-harbor position (17 U.S.C. § 512(c)) and for
 * registering the agent with the U.S. Copyright Office (§ 512(f) / Office
 * online registration). Remember to complete the *actual* registration with
 * the Copyright Office (dmca.copyright.gov) and keep the details here in
 * sync with the registration.
 */
export default function DmcaPolicyPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto py-12 px-4 sm:px-6">
        <Link href="/legal" className="text-sm text-gray-500 hover:text-gray-700">
          ← Legal
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mt-3">
          Copyright / DMCA Policy
        </h1>
        <p className="text-sm text-gray-500 mt-2">
          Last updated: September 27, 2026
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-gray-700">
          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              Designated DMCA Agent
            </h2>
            <p>
              ContractOS has designated an agent to receive notifications of
              claimed infringement under the Digital Millennium Copyright Act.
              Designated agent contact details:
            </p>
            <address className="not-italic mt-3 rounded-lg bg-white border border-gray-200 p-4">
              <div className="font-medium text-gray-900">
                DMCA Designated Agent
              </div>
              <div>ContractOS (Pvt) Ltd</div>
              <div>121 Galle Road, Colombo 03, Sri Lanka</div>
              <div>
                Email:{" "}
                <a
                  href={`mailto:${CONTACT_EMAIL}`}
                  className="text-blue-600 hover:text-blue-500"
                >
                  {CONTACT_EMAIL}
                </a>
              </div>
            </address>
            <p className="mt-3 text-xs text-gray-500">
              This agent is registered with the U.S. Copyright Office through
              its online DMCA designated-agent directory. Operator note: keep
              this page synchronized with the Copyright Office registration
              record and renew it when your service provider details change.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              Filing a Takedown Notice
            </h2>
            <p>
              If you believe content available through ContractOS infringes
              your copyright, send a written notice to the designated agent
              above that includes all of the following (17 U.S.C. § 512(c)(3)):
            </p>
            <ol className="list-decimal pl-5 space-y-1.5 mt-3">
              <li>
                A physical or electronic signature of the copyright owner or a
                person authorized to act on their behalf.
              </li>
              <li>
                Identification of the copyrighted work claimed to have been
                infringed, or a representative list if multiple works are
                covered.
              </li>
              <li>
                Identification of the material that is claimed to be
                infringing, with a URL or other information reasonably
                sufficient to locate it on the Service.
              </li>
              <li>
                Your contact information: name, mailing address, telephone
                number, and email address.
              </li>
              <li>
                A statement that you have a good-faith belief that use of the
                material in the manner complained of is not authorized by the
                copyright owner, its agent, or the law.
              </li>
              <li>
                A statement that the information in the notification is
                accurate, and under penalty of perjury, that you are authorized
                to act on behalf of the copyright owner.
              </li>
            </ol>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              Counter-Notification
            </h2>
            <p>
              If your content was removed as a result of a takedown notice and
              you believe the removal was a mistake or misidentification, you
              may submit a counter-notification to the designated agent that
              includes your signature, identification of the removed material
              and its former location, a statement under penalty of perjury
              that you have a good-faith belief the removal was a mistake, and
              your consent to the jurisdiction of the federal court for your
              district (or any judicial district in which ContractOS may be
              found) and to accepting service of process from the person who
              submitted the original notice (17 U.S.C. § 512(g)).
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              Repeat Infringers
            </h2>
            <p>
              In accordance with § 512(i), ContractOS reserves the right to
              terminate, in appropriate circumstances, the accounts of users
              who are determined to be repeat infringers.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">
              Misrepresentation Warning
            </h2>
            <p>
              Under § 512(f), any person who knowingly materially
              misrepresents that material is infringing, or that material was
              removed by mistake, may be liable for damages, including costs
              and attorneys&apos; fees.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
