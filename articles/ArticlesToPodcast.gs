/**
 * Articles → Podcast
 * Runs in the evaneasonpod@gmail.com account.
 *
 * Anything emailed to evaneasonpod@gmail.com becomes an episode
 * in your "Evan's Reading List" feed. You can send:
 *   - a forwarded email (newsletter, article someone sent you)
 *   - pasted article text
 *   - just a link to an article (works when the page isn't behind a paywall)
 *   - a .txt attachment
 */

// GitHub token (kept in Project Settings > Script Properties as GITHUB_TOKEN)
const ARTICLES_GITHUB_TOKEN = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
const ARTICLES_REPO = 'fredasboss/money-stuff-podcast';
const ARTICLES_DONE_LABEL = 'Podcast/Done';
const ARTICLES_MAX_CHARS = 90000;          // about 90 minutes of audio

function checkForArticles() {
  const done = GmailApp.getUserLabelByName(ARTICLES_DONE_LABEL) ||
               GmailApp.createLabel(ARTICLES_DONE_LABEL);

  // Remember which emails were already sent, so nothing is read twice
  const props = PropertiesService.getScriptProperties();
  let seen = JSON.parse(props.getProperty('ARTICLES_SEEN') || '[]');

  const threads = GmailApp.search('in:inbox newer_than:3d -from:google.com', 0, 20);

  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      if (seen.indexOf(msg.getId()) !== -1) return;

      let text = msg.getPlainBody() || '';
      msg.getAttachments().forEach(function (att) {
        if (/\.(txt|md)$/i.test(att.getName()) || att.getContentType() === 'text/plain') {
          text += '\n\n' + att.getDataAsString();
        }
      });
      if (text.length > ARTICLES_MAX_CHARS) text = text.substring(0, ARTICLES_MAX_CHARS);

      const ok = sendArticleToGitHub_({
        subject: msg.getSubject(),
        date: msg.getDate().toISOString(),
        text: text
      });
      if (!ok) throw new Error('GitHub did not accept the article; will retry next run.');
      seen.push(msg.getId());
      props.setProperty('ARTICLES_SEEN', JSON.stringify(seen.slice(-300)));
      thread.addLabel(done);
      msg.markRead();
    });
  });
}

function sendArticleToGitHub_(payload) {
  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + ARTICLES_REPO + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + ARTICLES_GITHUB_TOKEN,
      Accept: 'application/vnd.github+json'
    },
    payload: JSON.stringify({ event_type: 'new_article', client_payload: payload }),
    muteHttpExceptions: true
  });
  Logger.log('GitHub answered ' + res.getResponseCode() + ' for: ' + payload.subject);
  return res.getResponseCode() === 204;
}

/** Run this once by hand to make the script check every 10 minutes. */
function setUpArticleTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkForArticles') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('checkForArticles').timeBased().everyMinutes(10).create();

  // Skip whatever is already in the inbox (e.g. Google's welcome emails)
  const ids = [];
  GmailApp.search('in:inbox', 0, 100).forEach(function (t) {
    t.getMessages().forEach(function (m) { ids.push(m.getId()); });
  });
  PropertiesService.getScriptProperties().setProperty('ARTICLES_SEEN', JSON.stringify(ids));
}
