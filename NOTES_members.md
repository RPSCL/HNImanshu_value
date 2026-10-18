# Members panel: make a user prime / admin with one click

Open PF Builder as an admin: a "Members" table sits at the bottom of the page (admins only; prime members do not see it).
Every user who has ever logged in is listed (newest login first, search box on top). Click `Prime: off` to turn it on, click again to turn it off.
`Admin` works the same (asks to confirm before removing). The main admin@ account cannot be demoted.
It writes the same documents as before: `hni_prime/{uid}` and `hni_admins/{uid}`, so the Worker and the page pick it up at once
(the person just reloads PF Builder).

## One-time Firestore rules (only if the panel says "Not saved: ... Firestore rules")
Firebase console -> Firestore -> Rules. Inside `match /databases/{database}/documents { ... }` add (or merge into your existing hni_admins rule):

    function isAdmin() {
      return request.auth != null && (
        request.auth.token.email == 'admin@users.hnimanshu.app' ||
        exists(/databases/$(database)/documents/hni_admins/$(request.auth.uid)));
    }
    match /hni_admins/{uid} {
      allow read:  if request.auth != null && (request.auth.uid == uid || isAdmin());
      allow write: if isAdmin();
    }
    match /hni_prime/{uid} {
      allow read:  if request.auth != null && (request.auth.uid == uid || isAdmin());
      allow write: if isAdmin();
    }

Listing the users reads `hni_users`, which your Users list already does for admins, so that rule needs no change
(admins must be allowed to read the whole collection).
