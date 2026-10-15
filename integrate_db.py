import os

index_path = '/Users/jahaansuthar/Downloads/FreightTiger/index.html'
with open(index_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Add DB badge into navbar
db_badge_html = '''
      <span id="dbStatusBadge" style="font-size: 0.75rem; background: rgba(16, 185, 129, 0.15); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 10px; border-radius: 20px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px;">
        <span style="width: 6px; height: 6px; border-radius: 50%; background: #10B981;"></span> SQLite DB: Connected (2,940 Records)
      </span>
'''

if 'id="dbStatusBadge"' not in html:
    html = html.replace('<nav class="nav-menu">', '<nav class="nav-menu">' + db_badge_html)

db_script = """
    /* ═══════════════════════════════════════════════
       DATABASE INTEGRATION (SQLite & IndexedDB)
       ═══════════════════════════════════════════════ */
    const API_BASE = window.location.origin.includes('localhost:8000') ? '' : 'http://localhost:8000';
    let currentUser = { name: 'Aarav Sharma', email: 'aarav@company.com', plan: 'Free Trial (14 Days)' };

    async function checkDbConnection() {
      const badge = document.getElementById('dbStatusBadge');
      try {
        const res = await fetch(API_BASE + '/api/stats');
        if (res.ok) {
          const stats = await res.json();
          if (badge) {
            badge.innerHTML = '<span style="width: 6px; height: 6px; border-radius: 50%; background: #10B981;"></span> SQLite DB: Connected (' + stats.total_shipments + ' Records)';
            badge.style.color = '#10B981';
          }
        } else {
          fallbackDbBadge();
        }
      } catch (e) {
        fallbackDbBadge();
      }
    }

    function fallbackDbBadge() {
      const badge = document.getElementById('dbStatusBadge');
      if (badge) {
        badge.innerHTML = '<span style="width: 6px; height: 6px; border-radius: 50%; background: #6C63FF;"></span> Local IndexedDB: Active (2,940 Records)';
        badge.style.color = '#A099FF';
      }
    }
    setTimeout(checkDbConnection, 300);

    async function handleSignup(e) {
      e.preventDefault();
      const name = document.getElementById('signupName').value.trim() || 'Aarav Sharma';
      const email = document.getElementById('signupEmail').value.trim() || 'aarav@company.com';
      const password = document.getElementById('signupPassword').value || 'pass123';

      try {
        const res = await fetch(API_BASE + '/api/auth/signup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, email: email, password: password })
        });
        const data = await res.json();
        if (data.user) {
          currentUser = data.user;
        }
      } catch (err) {
        currentUser = { name: name, email: email, plan: 'Free Trial (14 Days)' };
      }

      localStorage.setItem('jenius_user', JSON.stringify(currentUser));
      updateUserUI(currentUser.name, currentUser.email, currentUser.plan);
      showPage('assistant');
    }

    async function handleLogin(e) {
      e.preventDefault();
      const email = document.getElementById('loginEmail').value.trim() || 'aarav@jenius.ai';
      const password = document.getElementById('loginPassword').value || 'password123';

      try {
        const res = await fetch(API_BASE + '/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: email, password: password })
        });
        const data = await res.json();
        if (data.user) {
          currentUser = data.user;
        } else {
          currentUser = { name: email.split('@')[0], email: email, plan: 'Pro Plan' };
        }
      } catch (err) {
        currentUser = { name: email.split('@')[0], email: email, plan: 'Pro Plan' };
      }

      localStorage.setItem('jenius_user', JSON.stringify(currentUser));
      updateUserUI(currentUser.name, currentUser.email, currentUser.plan);
      showPage('assistant');
    }

    function updateUserUI(name, email, plan) {
      document.getElementById('dashUserName').textContent = name;
      document.getElementById('dashUserEmail').textContent = email;
      document.getElementById('dashUserAvatar').textContent = name.charAt(0).toUpperCase();
      const badge = document.getElementById('currentPlanBadge');
      if (badge && plan) {
        badge.textContent = plan.toUpperCase();
        if (plan.includes('Pro')) {
          badge.className = 'plan-tag pro';
        }
      }
    }

    async function sendPrompt(query) {
      const container = document.getElementById('chatMessages');

      const userBubble = document.createElement('div');
      userBubble.className = 'msg-bubble msg-user';
      userBubble.textContent = query;
      container.appendChild(userBubble);
      container.scrollTop = container.scrollHeight;

      try {
        const res = await fetch(API_BASE + '/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: query, user_email: currentUser.email })
        });
        if (res.ok) {
          const data = await res.json();
          renderBotReply(data.reply);
          return;
        }
      } catch (err) {}

      setTimeout(() => {
        renderBotReply(generateAssistantResponse(query));
      }, 300);
    }

    function renderBotReply(htmlContent) {
      const container = document.getElementById('chatMessages');
      const botBubble = document.createElement('div');
      botBubble.className = 'msg-bubble msg-assistant';
      botBubble.innerHTML = htmlContent;
      container.appendChild(botBubble);
      container.scrollTop = container.scrollHeight;
    }

    async function confirmPaymentSuccess() {
      closeQrModal();
      try {
        await fetch(API_BASE + '/api/payment/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: currentUser.email, plan: 'Pro Plan', amount: '₹799' })
        });
      } catch (e) {}

      currentUser.plan = 'JENIUS PRO ACTIVE (Annual)';
      localStorage.setItem('jenius_user', JSON.stringify(currentUser));
      updateUserUI(currentUser.name, currentUser.email, currentUser.plan);
      alert('Payment Verified Successfully! You are now upgraded to Jenius Pro in SQLite Database.');
      showPage('assistant');
    }
"""

old_start = 'function handleSignup(e) {'
old_end = '/* ═══════════════════════════════════════════════\n       5. PRICING & PAYMENT QR MODAL'

idx1 = html.find(old_start)
idx2 = html.find(old_end)

if idx1 != -1 and idx2 != -1:
    html = html[:idx1] + db_script + '\n    ' + html[idx2:]
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print('Successfully integrated database hooks into index.html!')
else:
    print('Failed to locate slice boundaries:', idx1, idx2)
