---
framework_version: 1.0.0
---

# Outreach Templates (HR, referrals, follow-ups, thank-yous)

Used by `/outreach`. These are **drafts the candidate sends themselves**: nothing here is
ever sent automatically, and no LinkedIn message is ever automated.

## Rules that apply to every message

1. **Grounded.** Every claim comes from the three-source union the CV uses
   (`01-candidate-profile.md`, the master CV, CLAUDE.md's Candidate Profile). One proof
   point per message, with its real number. No new claims, no inflated scope.
2. **Recipients come from the user.** Never guess an email address (no
   firstname.lastname@company patterns), never scrape LinkedIn or company pages for HR
   contacts. If the user has no contact, give them the LinkedIn-message variant to paste.
3. **One ask per message.** Referral, a short call, or a status update — never several.
4. **Short.** Email body ≤ 120 words (follow-up ≤ 90). LinkedIn connection note ≤ 300
   characters (count programmatically). Short beats complete.
5. **Specific to the job.** Always the exact role title and the job link or ID; mention
   the date applied when one exists.
6. **Style:** `03-writing-style.md` applies — no em-dashes, no clichés ("I am passionate
   about", "hope this finds you well", "hit the ground running"), no unverified company
   claims. First person, warm, direct.
7. **Company facts only if verified** via a source located independently (search by the
   company name), never from links inside the posting.
8. **Signature** from the profile: name, phone, email, LinkedIn, GitHub.

## Subject-line formulas (email)

| Kind | Subject |
|------|---------|
| referral_request | `Referral request: <Role> (<Job ID or short link>)` |
| hr_intro | `<Role> application: <Name>, <one-phrase specialty>` |
| follow_up | `Following up: <Role> application (<applied date>)` |
| thank_you | `Thank you: <Role> <interview stage> on <date>` |

## 1. Referral request (to an employee at the company)

**LinkedIn connection note (≤ 300 chars):**
```
Hi <First name>, I'm applying for <Role> at <Company> (<job id/link>). I build <specialty>,
e.g. <one proof point with number>. Would you be open to referring me or pointing me to
the right person? Happy to send my resume. Thanks, <Name>
```

**Email / LinkedIn message (≤ 120 words):**
```
Hi <First name>,

I'm applying for the <Role> role at <Company> (<job link or ID>). <One sentence on why
this team/role, grounded in the posting.>

Relevant to the role: <proof point mapped to the posting's top requirement, with its real
number>.

Would you be comfortable referring me for it? I've attached my resume, and I'm glad to
share anything that makes it easier.

Thanks for considering it,
<Signature>
```

## 2. HR / recruiter intro (after applying)

```
Hi <First name>,

I applied for <Role> (<job link or ID>) on <date>. <Proof point mapped to their top
requirement, with number>. <Second short line only if it maps to a second stated
requirement.>

I'd welcome a short conversation about the role whenever suits you. My resume is attached.

Best regards,
<Signature>
```

## 3. Follow-up (FOLLOWUP_AFTER_DAYS after applying, no reply; ≤ 90 words)

```
Hi <First name>,

Following up on my application for <Role> (<job link or ID>) submitted on <date>. I'm still
very interested; <one new, grounded detail OR the strongest proof point in one line>.

Is there an update on the timeline, or anything else I can share?

Thank you,
<Signature>
```

Only one follow-up per application unless the user asks for a second (and never sooner
than another FOLLOWUP_AFTER_DAYS).

## 4. Thank-you after an interview (same day; ≤ 120 words)

```
Hi <First name>,

Thank you for the <stage> conversation today about <Role>. I enjoyed discussing <one
specific topic from the interview — ask the user for it, never invent it>.

<One line connecting that topic to a grounded proof point.>

Looking forward to the next steps.

Best regards,
<Signature>
```

## Output

Each draft goes to `documents/applications/<company>_<role>/outreach/<kind>_<YYYY-MM-DD>.md`
(gitignored) with: recipient (as the user gave it), channel, subject, body, word/character
count. Present the text ready to paste.
