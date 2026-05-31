import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms of Use | Misterr",
  description: "Website Terms of Use for misterr.ai.",
};

const LAST_REVISED = "May 30, 2026";
const VERSION = "1.0";

export default function TermsPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col px-6 py-16">
      <nav className="mb-10 text-sm">
        <Link
          href="/"
          className="text-neutral-600 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        >
          ← Back to home
        </Link>
      </nav>

      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        Website Terms of Use
      </h1>
      <p className="mt-2 text-sm text-neutral-500 dark:text-neutral-400">
        Version {VERSION} · Last revised on: {LAST_REVISED}
      </p>

      <div className="prose prose-neutral mt-10 max-w-none space-y-4 text-neutral-900">
        <p>
          The website located at www.misterr.ai (the “Site”) is a copyrighted
          work belonging to Antiff Inc (“Company”, “us”, “our”, and “we”).
          Certain features of the Site may be subject to additional guidelines,
          terms, or rules, which will be posted on the Site in connection with
          such features. All such additional terms, guidelines, and rules are
          incorporated by reference into these Terms.
        </p>
        <p>
          These Terms of Use (these “Terms”) set forth the legally binding
          terms and conditions that govern your use of the Site. By accessing
          or using the Site, you are accepting these Terms (on behalf of
          yourself or the entity that you represent), and you represent and
          warrant that you have the right, authority, and capacity to enter
          into these Terms (on behalf of yourself or the entity that you
          represent). you may not access or use the Site or accept the Terms
          if you are not at least 18 years old. If you do not agree with all
          of the provisions of these Terms, do not access and/or use the Site.
        </p>
        <p className="font-semibold uppercase">
          Please be aware that Section 8.2 contains provisions governing how
          to resolve disputes between you and Company. Among other things,
          Section 8.2 includes an agreement to arbitrate which requires, with
          limited exceptions, that all disputes between you and us shall be
          resolved by binding and final arbitration. Section 8.2 also contains
          a class action and jury trial waiver. Please read Section 8.2
          carefully.
        </p>
        <p className="font-semibold uppercase">
          Unless you opt out of the agreement to arbitrate within 30 days: (1)
          you will only be permitted to pursue disputes or claims and seek
          relief against us on an individual basis, not as a plaintiff or
          class member in any class or representative action or proceeding and
          you waive your right to participate in a class action lawsuit or
          class-wide arbitration; and (2) you are waiving your right to pursue
          disputes or claims and seek relief in a court of law and to have a
          jury trial.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          1. Accounts
        </h2>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          1.1 Account Creation
        </h3>
        <p>
          In order to use certain features of the Site, you must register for
          an account (“Account”) and provide certain information about
          yourself as prompted by the account registration form. You represent
          and warrant that: (a) all required registration information you
          submit is truthful and accurate; (b) you will maintain the accuracy
          of such information. You may delete your Account at any time, for
          any reason, by following the instructions on the Site. Company may
          suspend or terminate your Account in accordance with Section 7.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          1.2 Account Responsibilities
        </h3>
        <p>
          You are responsible for maintaining the confidentiality of your
          Account login information and are fully responsible for all
          activities that occur under your Account. You agree to immediately
          notify Company of any unauthorized use, or suspected unauthorized
          use of your Account or any other breach of security. Company cannot
          and will not be liable for any loss or damage arising from your
          failure to comply with the above requirements.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          2. Access to the Site
        </h2>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.1 License
        </h3>
        <p>
          Subject to these Terms, Company grants you a non-transferable,
          non-exclusive, revocable, limited license to use and access the Site
          solely for your own personal, noncommercial use.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.2 Certain Restrictions
        </h3>
        <p>
          The rights granted to you in these Terms are subject to the
          following restrictions: (a) you shall not license, sell, rent,
          lease, transfer, assign, distribute, host, or otherwise commercially
          exploit the Site, whether in whole or in part, or any content
          displayed on the Site; (b) you shall not modify, make derivative
          works of, disassemble, reverse compile or reverse engineer any part
          of the Site; (c) you shall not access the Site in order to build a
          similar or competitive website, product, or service; and (d) except
          as expressly stated herein, no part of the Site may be copied,
          reproduced, distributed, republished, downloaded, displayed, posted
          or transmitted in any form or by any means. Unless otherwise
          indicated, any future release, update, or other addition to
          functionality of the Site shall be subject to these Terms. All
          copyright and other proprietary notices on the Site (or on any
          content displayed on the Site) must be retained on all copies
          thereof.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.3 Modification
        </h3>
        <p>
          Company reserves the right, at any time, to modify, suspend, or
          discontinue the Site (in whole or in part) with or without notice to
          you. You agree that Company will not be liable to you or to any
          third party for any modification, suspension, or discontinuation of
          the Site or any part thereof.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.4 No Support or Maintenance
        </h3>
        <p>
          You acknowledge and agree that Company will have no obligation to
          provide you with any support or maintenance in connection with the
          Site.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.5 Ownership
        </h3>
        <p>
          You acknowledge that all the intellectual property rights, including
          copyrights, patents, trade marks, and trade secrets, in the Site and
          its content are owned by Company or Company’s suppliers. Neither
          these Terms (nor your access to the Site) transfers to you or any
          third party any rights, title or interest in or to such intellectual
          property rights, except for the limited access rights expressly set
          forth in Section 2.1. Company and its suppliers reserve all rights
          not granted in these Terms. There are no implied licenses granted
          under these Terms.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          2.6 Feedback
        </h3>
        <p>
          If you provide Company with any feedback or suggestions regarding
          the Site (“Feedback”), you hereby assign to Company all rights in
          such Feedback and agree that Company shall have the right to use and
          fully exploit such Feedback and related information in any manner it
          deems appropriate. Company will treat any Feedback you provide to
          Company as non-confidential and non-proprietary. You agree that you
          will not submit to Company any information or ideas that you
          consider to be confidential or proprietary.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          3. Indemnification
        </h2>
        <p>
          You agree to indemnify and hold Company (and its officers,
          employees, and agents) harmless, including costs and attorneys’
          fees, from any claim or demand made by any third party due to or
          arising out of (a) your use of the Site, (b) your violation of these
          Terms or (c) your violation of applicable laws or regulations.
          Company reserves the right, at your expense, to assume the exclusive
          defense and control of any matter for which you are required to
          indemnify us, and you agree to cooperate with our defense of these
          claims. You agree not to settle any matter without the prior written
          consent of Company. Company will use reasonable efforts to notify
          you of any such claim, action or proceeding upon becoming aware of
          it.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          4. Third-Party Links &amp; Ads; Other Users
        </h2>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          4.1 Third-Party Links &amp; Ads
        </h3>
        <p>
          The Site may contain links to third-party websites and services,
          and/or display advertisements for third parties (collectively,
          “Third-Party Links &amp; Ads”). Such Third-Party Links &amp; Ads are
          not under the control of Company, and Company is not responsible
          for any Third-Party Links &amp; Ads. Company provides access to
          these Third-Party Links &amp; Ads only as a convenience to you, and
          does not review, approve, monitor, endorse, warrant, or make any
          representations with respect to Third-Party Links &amp; Ads. You use
          all Third-Party Links &amp; Ads at your own risk, and should apply a
          suitable level of caution and discretion in doing so. When you click
          on any of the Third-Party Links &amp; Ads, the applicable third
          party’s terms and policies apply, including the third party’s
          privacy and data gathering practices. You should make whatever
          investigation you feel necessary or appropriate before proceeding
          with any transaction in connection with such Third-Party Links &amp;
          Ads.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          4.2 Other Users
        </h3>
        <p>
          Your interactions with other Site users are solely between you and
          such users. You agree that Company will not be responsible for any
          loss or damage incurred as the result of any such interactions. If
          there is a dispute between you and any Site user, we are under no
          obligation to become involved.
        </p>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          4.3 Release
        </h3>
        <p>
          You hereby release and forever discharge Company (and our officers,
          employees, agents, successors, and assigns) from, and hereby waive
          and relinquish, each and every past, present and future dispute,
          claim, controversy, demand, right, obligation, liability, action and
          cause of action of every kind and nature (including personal
          injuries, death, and property damage), that has arisen or arises
          directly or indirectly out of, or that relates directly or
          indirectly to, the Site (including any interactions with, or act or
          omission of, other Site users or any Third-Party Links &amp; Ads).
          If you are a California resident, you hereby waive California Civil
          Code Section 1542 in connection with the foregoing, which states: “A
          general release does not extend to claims which the creditor or
          releasing party does not know or suspect to exist in his or her
          favor at the time of executing the release, which if known by him
          or her must have materially affected his or her settlement with the
          debtor or released party.”
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          5. Disclaimers
        </h2>
        <p className="uppercase">
          The Site is provided on an “as-is” and “as available” basis, and
          Company (and our suppliers) expressly disclaim any and all
          warranties and conditions of any kind, whether express, implied, or
          statutory, including all warranties or conditions of
          merchantability, fitness for a particular purpose, title, quiet
          enjoyment, accuracy, or non-infringement. We (and our suppliers)
          make no warranty that the Site will meet your requirements, will be
          available on an uninterrupted, timely, secure, or error-free basis,
          or will be accurate, reliable, free of viruses or other harmful
          code, complete, legal, or safe. If applicable law requires any
          warranties with respect to the Site, all such warranties are
          limited in duration to 90 days from the date of first use.
        </p>
        <p className="uppercase">
          Some jurisdictions do not allow the exclusion of implied warranties,
          so the above exclusion may not apply to you. Some jurisdictions do
          not allow limitations on how long an implied warranty lasts, so the
          above limitation may not apply to you.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          6. Limitation on Liability
        </h2>
        <p className="uppercase">
          To the maximum extent permitted by law, in no event shall Company
          (or our suppliers) be liable to you or any third party for any lost
          profits, lost data, costs of procurement of substitute products, or
          any indirect, consequential, exemplary, incidental, special or
          punitive damages arising from or relating to these Terms or your
          use of, or inability to use, the Site, even if Company has been
          advised of the possibility of such damages. Access to, and use of,
          the Site is at your own discretion and risk, and you will be solely
          responsible for any damage to your device or computer system, or
          loss of data resulting therefrom.
        </p>
        <p className="uppercase">
          To the maximum extent permitted by law, notwithstanding anything to
          the contrary contained herein, our liability to you for any damages
          arising from or related to these Terms (for any cause whatsoever
          and regardless of the form of the action), will at all times be
          limited to a maximum of fifty US Dollars. The existence of more
          than one claim will not enlarge this limit. You agree that our
          suppliers will have no liability of any kind arising from or
          relating to these Terms.
        </p>
        <p className="uppercase">
          Some jurisdictions do not allow the limitation or exclusion of
          liability for incidental or consequential damages, so the above
          limitation or exclusion may not apply to you.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          7. Term and Termination
        </h2>
        <p>
          Subject to this Section, these Terms will remain in full force and
          effect while you use the Site. We may suspend or terminate your
          rights to use the Site (including your Account) at any time for any
          reason at our sole discretion, including for any use of the Site in
          violation of these Terms. Upon termination of your rights under
          these Terms, your Account and right to access and use the Site will
          terminate immediately. Company will not have any liability
          whatsoever to you for any termination of your rights under these
          Terms, including for termination of your Account. Even after your
          rights under these Terms are terminated, the following provisions
          of these Terms will remain in effect: Sections 2.2 through 2.6 and
          Sections 3 through 8.
        </p>

        <h2 className="mt-10 text-2xl font-semibold text-neutral-900 dark:text-neutral-100">
          8. General
        </h2>
        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.1 Changes
        </h3>
        <p>
          These Terms are subject to occasional revision, and if we make any
          substantial changes, we may notify you by sending you an e-mail to
          the last e-mail address you provided to us (if any), and/or by
          prominently posting notice of the changes on our Site. You are
          responsible for providing us with your most current e-mail address.
          In the event that the last e-mail address that you have provided us
          is not valid, or for any reason is not capable of delivering to you
          the notice described above, our dispatch of the e-mail containing
          such notice will nonetheless constitute effective notice of the
          changes described in the notice. Continued use of our Site
          following notice of such changes shall indicate your acknowledgement
          of such changes and agreement to be bound by the terms and
          conditions of such changes.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.2 Dispute Resolution
        </h3>
        <p>
          Please read the following arbitration agreement in this Section
          (the “Arbitration Agreement”) carefully. It requires you to
          arbitrate disputes with Company, its parent companies, subsidiaries,
          affiliates, successors and assigns and all of their respective
          officers, directors, employees, agents, and representatives
          (collectively, the “Company Parties”) and limits the manner in
          which you can seek relief from the Company Parties.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (a) Applicability of Arbitration Agreement
        </h4>
        <p>
          You agree that any dispute between you and any of the Company
          Parties relating in any way to the Site, the services offered on
          the Site (the “Services”) or these Terms will be resolved by
          binding arbitration, rather than in court, except that (1) you and
          the Company Parties may assert individualized claims in small
          claims court if the claims qualify, remain in such court and
          advance solely on an individual, non-class basis; and (2) you or
          the Company Parties may seek equitable relief in court for
          infringement or other misuse of intellectual property rights (such
          as trademarks, trade dress, domain names, trade secrets,
          copyrights, and patents). This Arbitration Agreement shall survive
          the expiration or termination of these Terms and shall apply,
          without limitation, to all claims that arose or were asserted
          before you agreed to these Terms (in accordance with the preamble)
          or any prior version of these Terms. This Arbitration Agreement
          does not preclude you from bringing issues to the attention of
          federal, state or local agencies. Such agencies can, if the law
          allows, seek relief against the Company Parties on your behalf. For
          purposes of this Arbitration Agreement, “Dispute” will also include
          disputes that arose or involve facts occurring before the existence
          of this or any prior versions of the Agreement as well as claims
          that may arise after the termination of these Terms.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (b) Informal Dispute Resolution
        </h4>
        <p>
          There might be instances when a Dispute arises between you and
          Company. If that occurs, Company is committed to working with you
          to reach a reasonable resolution. You and Company agree that good
          faith informal efforts to resolve Disputes can result in a prompt,
          low-cost and mutually beneficial outcome. You and Company therefore
          agree that before either party commences arbitration against the
          other (or initiates an action in small claims court if a party so
          elects), we will personally meet and confer telephonically or via
          videoconference, in a good faith effort to resolve informally any
          Dispute covered by this Arbitration Agreement (“Informal Dispute
          Resolution Conference”). If you are represented by counsel, your
          counsel may participate in the conference, but you will also
          participate in the conference.
        </p>
        <p>
          The party initiating a Dispute must give notice to the other party
          in writing of its intent to initiate an Informal Dispute Resolution
          Conference (“Notice”), which shall occur within 45 days after the
          other party receives such Notice, unless an extension is mutually
          agreed upon by the parties. Notice to Company that you intend to
          initiate an Informal Dispute Resolution Conference should be sent
          by email to:{" "}
          <a
            href="mailto:support@misterr.ai"
            className="underline hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            support@misterr.ai
          </a>
          , or by regular mail to 2810 North Church Street, Wilmington,
          Delaware 19802. The Notice must include: (1) your name, telephone
          number, mailing address, e-mail address associated with your
          account (if you have one); (2) the name, telephone number, mailing
          address and e-mail address of your counsel, if any; and (3) a
          description of your Dispute.
        </p>
        <p>
          The Informal Dispute Resolution Conference shall be individualized
          such that a separate conference must be held each time either party
          initiates a Dispute, even if the same law firm or group of law
          firms represents multiple users in similar cases, unless all
          parties agree; multiple individuals initiating a Dispute cannot
          participate in the same Informal Dispute Resolution Conference
          unless all parties agree. In the time between a party receiving the
          Notice and the Informal Dispute Resolution Conference, nothing in
          this Arbitration Agreement shall prohibit the parties from engaging
          in informal communications to resolve the initiating party’s
          Dispute. Engaging in the Informal Dispute Resolution Conference is
          a condition precedent and requirement that must be fulfilled before
          commencing arbitration. The statute of limitations and any filing
          fee deadlines shall be tolled while the parties engage in the
          Informal Dispute Resolution Conference process required by this
          section.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (c) Arbitration Rules and Forum
        </h4>
        <p>
          These Terms evidence a transaction involving interstate commerce;
          and notwithstanding any other provision herein with respect to the
          applicable substantive law, the Federal Arbitration Act, 9 U.S.C.
          § 1 et seq., will govern the interpretation and enforcement of
          this Arbitration Agreement and any arbitration proceedings. If the
          Informal Dispute Resolution Process described above does not
          resolve satisfactorily within 60 days after receipt of your Notice,
          you and Company agree that either party shall have the right to
          finally resolve the Dispute through binding arbitration. The
          Federal Arbitration Act governs the interpretation and enforcement
          of this Arbitration Agreement. The arbitration will be conducted by
          JAMS, an established alternative dispute resolution provider.
          Disputes involving claims and counterclaims with an amount in
          controversy under $250,000, not inclusive of attorneys’ fees and
          interest, shall be subject to JAMS’ most current version of the
          Streamlined Arbitration Rules and procedures available at
          http://www.jamsadr.com/rules-streamlined-arbitration/; all other
          claims shall be subject to JAMS’s most current version of the
          Comprehensive Arbitration Rules and Procedures, available at
          http://www.jamsadr.com/rules-comprehensive-arbitration/. JAMS’s
          rules are also available at www.jamsadr.com or by calling JAMS at
          800-352-5267. A party who wishes to initiate arbitration must
          provide the other party with a request for arbitration (the
          “Request”). The Request must include: (1) the name, telephone
          number, mailing address, e-mail address of the party seeking
          arbitration and the account username (if applicable) as well as
          the email address associated with any applicable account; (2) a
          statement of the legal claims being asserted and the factual bases
          of those claims; (3) a description of the remedy sought and an
          accurate, good-faith calculation of the amount in controversy in
          United States Dollars; (4) a statement certifying completion of
          the Informal Dispute Resolution process as described above; and
          (5) evidence that the requesting party has paid any necessary
          filing fees in connection with such arbitration.
        </p>
        <p>
          If the party requesting arbitration is represented by counsel, the
          Request shall also include counsel’s name, telephone number,
          mailing address, and email address. Such counsel must also sign the
          Request. By signing the Request, counsel certifies to the best of
          counsel’s knowledge, information, and belief, formed after an
          inquiry reasonable under the circumstances, that: (1) the Request
          is not being presented for any improper purpose, such as to harass,
          cause unnecessary delay, or needlessly increase the cost of dispute
          resolution; (2) the claims, defenses and other legal contentions
          are warranted by existing law or by a nonfrivolous argument for
          extending, modifying, or reversing existing law or for establishing
          new law; and (3) the factual and damages contentions have
          evidentiary support or, if specifically so identified, will likely
          have evidentiary support after a reasonable opportunity for further
          investigation or discovery.
        </p>
        <p>
          Unless you and Company otherwise agree, or the Batch Arbitration
          process discussed in Subsection 8.2(h) is triggered, the
          arbitration will be conducted in the county where you reside.
          Subject to the JAMS Rules, the arbitrator may direct a limited and
          reasonable exchange of information between the parties, consistent
          with the expedited nature of the arbitration. If the JAMS is not
          available to arbitrate, the parties will select an alternative
          arbitral forum. Your responsibility to pay any JAMS fees and costs
          will be solely as set forth in the applicable JAMS Rules.
        </p>
        <p>
          You and Company agree that all materials and documents exchanged
          during the arbitration proceedings shall be kept confidential and
          shall not be shared with anyone except the parties’ attorneys,
          accountants, or business advisors, and then subject to the
          condition that they agree to keep all materials and documents
          exchanged during the arbitration proceedings confidential.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (d) Authority of Arbitrator
        </h4>
        <p>
          The arbitrator shall have exclusive authority to resolve all
          disputes subject to arbitration hereunder including, without
          limitation, any dispute related to the interpretation,
          applicability, enforceability or formation of this Arbitration
          Agreement or any portion of the Arbitration Agreement, except for
          the following: (1) all Disputes arising out of or relating to the
          subsection entitled “Waiver of Class or Other Non-Individualized
          Relief,” including any claim that all or part of the subsection
          entitled “Waiver of Class or Other Non-Individualized Relief” is
          unenforceable, illegal, void or voidable, or that such subsection
          entitled “Waiver of Class or Other Non-Individualized Relief” has
          been breached, shall be decided by a court of competent
          jurisdiction and not by an arbitrator; (2) except as expressly
          contemplated in the subsection entitled “Batch Arbitration,” all
          Disputes about the payment of arbitration fees shall be decided
          only by a court of competent jurisdiction and not by an arbitrator;
          (3) all Disputes about whether either party has satisfied any
          condition precedent to arbitration shall be decided only by a
          court of competent jurisdiction and not by an arbitrator; and (4)
          all Disputes about which version of the Arbitration Agreement
          applies shall be decided only by a court of competent jurisdiction
          and not by an arbitrator. The arbitration proceeding will not be
          consolidated with any other matters or joined with any other cases
          or parties, except as expressly provided in the subsection
          entitled “Batch Arbitration.” The arbitrator shall have the
          authority to grant motions dispositive of all or part of any claim
          or dispute. The arbitrator shall have the authority to award
          monetary damages and to grant any non-monetary remedy or relief
          available to an individual party under applicable law, the
          arbitral forum’s rules, and these Terms (including the Arbitration
          Agreement). The arbitrator shall issue a written award and
          statement of decision describing the essential findings and
          conclusions on which any award (or decision not to render an
          award) is based, including the calculation of any damages awarded.
          The arbitrator shall follow the applicable law. The award of the
          arbitrator is final and binding upon you and us. Judgment on the
          arbitration award may be entered in any court having jurisdiction.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (e) Waiver of Jury Trial
        </h4>
        <p>
          <span className="uppercase">
            Except as specified in Section 8.2(a), you and the Company
            Parties hereby waive any constitutional and statutory rights to
            sue in court and have a trial in front of a judge or a jury.
          </span>{" "}
          You and the Company Parties are instead electing that all covered
          claims and disputes shall be resolved exclusively by arbitration
          under this Arbitration Agreement, except as specified in Section
          8.2(a) above. An arbitrator can award on an individual basis the
          same damages and relief as a court and must follow these Terms as
          a court would. However, there is no judge or jury in arbitration,
          and court review of an arbitration award is subject to very
          limited review.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (f) Waiver of Class or Other Non-Individualized Relief
        </h4>
        <p>
          <span className="uppercase">
            You and Company agree that, except as specified in subsection
            8.2(h), each of us may bring claims against the other only on an
            individual basis and not on a class, representative, or
            collective basis, and the parties hereby waive all rights to
            have any dispute be brought, heard, administered, resolved, or
            arbitrated on a class, collective, representative, or mass
            action basis. Only individual relief is available, and disputes
            of more than one customer or user cannot be arbitrated or
            consolidated with those of any other customer or user.
          </span>{" "}
          Subject to this Arbitration Agreement, the arbitrator may award
          declaratory or injunctive relief only in favor of the individual
          party seeking relief and only to the extent necessary to provide
          relief warranted by the party’s individual claim. Nothing in this
          paragraph is intended to, nor shall it, affect the terms and
          conditions under the Subsection 8.2(h) entitled “Batch
          Arbitration.” Notwithstanding anything to the contrary in this
          Arbitration Agreement, if a court decides by means of a final
          decision, not subject to any further appeal or recourse, that the
          limitations of this subsection, “Waiver of Class or Other
          Non-Individualized Relief,” are invalid or unenforceable as to a
          particular claim or request for relief (such as a request for
          public injunctive relief), you and Company agree that that
          particular claim or request for relief (and only that particular
          claim or request for relief) shall be severed from the arbitration
          and may be litigated in the state or federal courts located in the
          State of Delaware. All other Disputes shall be arbitrated or
          litigated in small claims court. This subsection does not prevent
          you or Company from participating in a class-wide settlement of
          claims.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (g) Attorneys’ Fees and Costs
        </h4>
        <p>
          The parties shall bear their own attorneys’ fees and costs in
          arbitration unless the arbitrator finds that either the substance
          of the Dispute or the relief sought in the Request was frivolous
          or was brought for an improper purpose (as measured by the
          standards set forth in Federal Rule of Civil Procedure 11(b)). If
          you or Company need to invoke the authority of a court of
          competent jurisdiction to compel arbitration, then the party that
          obtains an order compelling arbitration in such action shall have
          the right to collect from the other party its reasonable costs,
          necessary disbursements, and reasonable attorneys’ fees incurred
          in securing an order compelling arbitration. The prevailing party
          in any court action relating to whether either party has satisfied
          any condition precedent to arbitration, including the Informal
          Dispute Resolution Process, is entitled to recover their
          reasonable costs, necessary disbursements, and reasonable
          attorneys’ fees and costs.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (h) Batch Arbitration
        </h4>
        <p>
          To increase the efficiency of administration and resolution of
          arbitrations, you and Company agree that in the event that there
          are 100 or more individual Requests of a substantially similar
          nature filed against Company by or with the assistance of the same
          law firm, group of law firms, or organizations, within a 30 day
          period (or as soon as possible thereafter), the JAMS shall (1)
          administer the arbitration demands in batches of 100 Requests per
          batch (plus, to the extent there are less than 100 Requests left
          over after the batching described above, a final batch consisting
          of the remaining Requests); (2) appoint one arbitrator for each
          batch; and (3) provide for the resolution of each batch as a
          single consolidated arbitration with one set of filing and
          administrative fees due per side per batch, one procedural
          calendar, one hearing (if any) in a place to be determined by the
          arbitrator, and one final award (“Batch Arbitration”).
        </p>
        <p>
          All parties agree that Requests are of a “substantially similar
          nature” if they arise out of or relate to the same event or
          factual scenario and raise the same or similar legal issues and
          seek the same or similar relief. To the extent the parties
          disagree on the application of the Batch Arbitration process, the
          disagreeing party shall advise the JAMS, and the JAMS shall
          appoint a sole standing arbitrator to determine the applicability
          of the Batch Arbitration process (“Administrative Arbitrator”).
          In an effort to expedite resolution of any such dispute by the
          Administrative Arbitrator, the parties agree the Administrative
          Arbitrator may set forth such procedures as are necessary to
          resolve any disputes promptly. The Administrative Arbitrator’s
          fees shall be paid by Company.
        </p>
        <p>
          You and Company agree to cooperate in good faith with the JAMS to
          implement the Batch Arbitration process including the payment of
          single filing and administrative fees for batches of Requests, as
          well as any steps to minimize the time and costs of arbitration,
          which may include: (1) the appointment of a discovery special
          master to assist the arbitrator in the resolution of discovery
          disputes; and (2) the adoption of an expedited calendar of the
          arbitration proceedings.
        </p>
        <p>
          This Batch Arbitration provision shall in no way be interpreted as
          authorizing a class, collective and/or mass arbitration or action
          of any kind, or arbitration involving joint or consolidated claims
          under any circumstances, except as expressly set forth in this
          provision.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (i) 30-Day Right to Opt Out
        </h4>
        <p>
          You have the right to opt out of the provisions of this
          Arbitration Agreement by sending a timely written notice of your
          decision to opt out to the following address: 2810 North Church
          Street, Wilmington, Delaware 19802, or email to{" "}
          <a
            href="mailto:support@misterr.ai"
            className="underline hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            support@misterr.ai
          </a>
          , within 30 days after first becoming subject to this Arbitration
          Agreement. Your notice must include your name and address and a
          clear statement that you want to opt out of this Arbitration
          Agreement. If you opt out of this Arbitration Agreement, all
          other parts of these Terms will continue to apply to you. Opting
          out of this Arbitration Agreement has no effect on any other
          arbitration agreements that you may currently have with us, or
          may enter into in the future with us.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (j) Invalidity, Expiration
        </h4>
        <p>
          Except as provided in the subsection entitled “Waiver of Class or
          Other Non-Individualized Relief”, if any part or parts of this
          Arbitration Agreement are found under the law to be invalid or
          unenforceable, then such specific part or parts shall be of no
          force and effect and shall be severed and the remainder of the
          Arbitration Agreement shall continue in full force and effect. You
          further agree that any Dispute that you have with Company as
          detailed in this Arbitration Agreement must be initiated via
          arbitration within the applicable statute of limitation for that
          claim or controversy, or it will be forever time barred. Likewise,
          you agree that all applicable statutes of limitation will apply to
          such arbitration in the same manner as those statutes of
          limitation would apply in the applicable court of competent
          jurisdiction.
        </p>

        <h4 className="mt-4 font-semibold text-neutral-900 dark:text-neutral-100">
          (k) Modification
        </h4>
        <p>
          Notwithstanding any provision in these Terms to the contrary, we
          agree that if Company makes any future material change to this
          Arbitration Agreement, you may reject that change within 30 days
          of such change becoming effective by writing Company at the
          following address: 2810 North Church Street, Wilmington, Delaware
          19802, or email to{" "}
          <a
            href="mailto:support@misterr.ai"
            className="underline hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            support@misterr.ai
          </a>
          . Unless you reject the change within 30 days of such change
          becoming effective by writing to Company in accordance with the
          foregoing, your continued use of the Site and/or Services,
          including the acceptance of products and services offered on the
          Site following the posting of changes to this Arbitration
          Agreement constitutes your acceptance of any such changes. Changes
          to this Arbitration Agreement do not provide you with a new
          opportunity to opt out of the Arbitration Agreement if you have
          previously agreed to a version of these Terms and did not validly
          opt out of arbitration. If you reject any change or update to
          this Arbitration Agreement, and you were bound by an existing
          agreement to arbitrate Disputes arising out of or relating in any
          way to your access to or use of the Services or of the Site, any
          communications you receive, any products sold or distributed
          through the Site, the Services, or these Terms, the provisions of
          this Arbitration Agreement as of the date you first accepted these
          Terms (or accepted any subsequent changes to these Terms) remain
          in full force and effect. Company will continue to honor any valid
          opt outs of the Arbitration Agreement that you made to a prior
          version of these Terms.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.3 Export
        </h3>
        <p>
          The Site may be subject to U.S. export control laws and may be
          subject to export or import regulations in other countries. You
          agree not to export, reexport, or transfer, directly or
          indirectly, any U.S. technical data acquired from Company, or any
          products utilizing such data, in violation of the United States
          export laws or regulations.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.4 Disclosures
        </h3>
        <p>
          Company is located at the address in Section 8.8. If you are a
          California resident, you may report complaints to the Complaint
          Assistance Unit of the Division of Consumer Product of the
          California Department of Consumer Affairs by contacting them in
          writing at 400 R Street, Sacramento, CA 95814, or by telephone at
          (800) 952-5210.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.5 Electronic Communications
        </h3>
        <p>
          The communications between you and Company use electronic means,
          whether you use the Site or send us emails, or whether Company
          posts notices on the Site or communicates with you via email. For
          contractual purposes, you (a) consent to receive communications
          from Company in an electronic form; and (b) agree that all terms
          and conditions, agreements, notices, disclosures, and other
          communications that Company provides to you electronically satisfy
          any legal requirement that such communications would satisfy if it
          were be in a hardcopy writing. The foregoing does not affect your
          non-waivable rights.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.6 Entire Terms
        </h3>
        <p>
          These Terms constitute the entire agreement between you and us
          regarding the use of the Site. Our failure to exercise or enforce
          any right or provision of these Terms shall not operate as a
          waiver of such right or provision. The section titles in these
          Terms are for convenience only and have no legal or contractual
          effect. The word “including” means “including without limitation”.
          If any provision of these Terms is, for any reason, held to be
          invalid or unenforceable, the other provisions of these Terms will
          be unimpaired and the invalid or unenforceable provision will be
          deemed modified so that it is valid and enforceable to the maximum
          extent permitted by law. Your relationship to Company is that of
          an independent contractor, and neither party is an agent or
          partner of the other. These Terms, and your rights and obligations
          herein, may not be assigned, subcontracted, delegated, or
          otherwise transferred by you without Company’s prior written
          consent, and any attempted assignment, subcontract, delegation, or
          transfer in violation of the foregoing will be null and void.
          Company may freely assign these Terms. The terms and conditions
          set forth in these Terms shall be binding upon assignees.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.7 Copyright/Trademark Information
        </h3>
        <p>
          Copyright © 2026 Antiff Inc. All rights reserved. All trademarks,
          logos and service marks (“Marks”) displayed on the Site are our
          property or the property of other third parties. You are not
          permitted to use these Marks without our prior written consent or
          the consent of such third party which may own the Marks.
        </p>

        <h3 className="mt-6 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          8.8 Contact Information
        </h3>
        <address className="not-italic">
          <p>Antiff Inc</p>
          <p>2810 North Church Street</p>
          <p>Wilmington, Delaware 19802</p>
          <p>
            Email:{" "}
            <a
              href="mailto:support@misterr.ai"
              className="underline hover:text-neutral-900 dark:hover:text-neutral-100"
            >
              support@misterr.ai
            </a>
          </p>
        </address>
      </div>
    </main>
  );
}
