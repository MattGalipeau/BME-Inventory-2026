async function performSearch(query) {
    const response = await fetch('/search', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ search_query: query })
    });
    const results = await response.json();
    displayResults(results);
}

function displayResults(results) {
    const resultContainer = document.querySelector('.results');
    resultContainer.innerHTML = '';

    if (results.length > 0) {
        results.forEach(result => {
            const resultDiv = document.createElement('div');
            resultDiv.classList.add('result-each');
            resultDiv.innerHTML = `
                <p class="name"><strong>${result.Name}</strong></p>
                <p class="item-info"><strong>Total Quantity: </strong>${result.TotalQty}</p>
                <p class="item-info"><strong># of Locations: </strong>${result.LocationCount}</p>
                <p class="item-info"><strong>UPC: </strong>${result.UPC}</p>
            `;
            resultContainer.appendChild(resultDiv);
        });
    } else {
        resultContainer.innerHTML = '<p class="search-error">No matches found.</p>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search_query');
    searchInput.addEventListener('input', (event) => {
        const query = event.target.value;
        if (query) {
            performSearch(query);
        } else {
            document.querySelector('.results').innerHTML = '';
        }
    });
    const modal = document.getElementById('itemModal');
  const closeButton = document.querySelector('.close-button');

  // Attach event listeners to the cards
  document.querySelector('.results').addEventListener('click', (event) => {
      const card = event.target.closest('.result-each');
      if (card) {
          const itemName = card.querySelector('.name').innerText;
          const itemInfo = Array.from(card.querySelectorAll('.item-info')).map(info => info.innerText).join('\n');

          document.getElementById('itemTitle').innerText = itemName;
          document.getElementById('itemDetails').innerText = itemInfo;

          // Show the modal
          modal.style.display = 'block';
      }
  });

  // Close modal on clicking close button
  closeButton.addEventListener('click', () => {
      modal.style.display = 'none';
  });

  // Close modal when clicking outside the modal content
  modal.addEventListener('click', (event) => {
      if (event.target === modal) {
          modal.style.display = 'none';
      }
  });

  const helpbotLauncher = document.getElementById('helpbot-launcher');
  const helpbotClose = document.getElementById('helpbot-close');
  const helpbotPanel = document.getElementById('helpbot-panel');
  const helpbotForm = document.getElementById('helpbot-form');
  const helpbotInput = document.getElementById('helpbot-input');
  const helpbotMessages = document.getElementById('helpbot-messages');
  const helpbotHistory = [
      {
          role: 'assistant',
          content: 'I can help locate items, summarize quantities, tell you what is in a room, and suggest which inventory items fit a task like 3D printing.'
      }
  ];

  function setHelpbotOpen(isOpen) {
      helpbotPanel.classList.toggle('hidden', !isOpen);
      helpbotLauncher.setAttribute('aria-expanded', String(isOpen));
      if (isOpen) {
          helpbotInput.focus();
      }
  }

  function appendHelpbotMessage(role, text, sources = []) {
      const message = document.createElement('div');
      message.classList.add('helpbot-message', role);
      const body = document.createElement('div');
      body.textContent = text;
      message.appendChild(body);

      if (sources.length > 0) {
          const sourceList = document.createElement('div');
          sourceList.classList.add('helpbot-sources');
          const label = document.createElement('div');
          label.classList.add('helpbot-sources-label');
          label.textContent = 'Sources';
          sourceList.appendChild(label);

          sources.forEach((source) => {
              const link = document.createElement('a');
              link.href = source.url;
              link.target = '_blank';
              link.rel = 'noreferrer';
              link.textContent = source.title || source.url;
              sourceList.appendChild(link);
          });

          message.appendChild(sourceList);
      }

      helpbotMessages.appendChild(message);
      helpbotMessages.scrollTop = helpbotMessages.scrollHeight;
      return message;
  }

  helpbotLauncher.addEventListener('click', () => {
      const isOpen = helpbotPanel.classList.contains('hidden');
      setHelpbotOpen(isOpen);
  });

  helpbotClose.addEventListener('click', () => {
      setHelpbotOpen(false);
  });

  helpbotForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = helpbotInput.value.trim();
      if (!message) {
          return;
      }

      appendHelpbotMessage('user', message);
      helpbotInput.value = '';
      const pendingMessage = appendHelpbotMessage('bot', 'Looking that up...');

      try {
          const response = await fetch('/help-chat', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  message,
                  history: helpbotHistory
              })
          });

          const data = await response.json();
          pendingMessage.textContent = '';
          pendingMessage.appendChild(document.createTextNode(data.reply || 'I could not find anything for that request.'));

          if (Array.isArray(data.sources) && data.sources.length > 0) {
              const sourceList = document.createElement('div');
              sourceList.classList.add('helpbot-sources');
              const label = document.createElement('div');
              label.classList.add('helpbot-sources-label');
              label.textContent = 'Sources';
              sourceList.appendChild(label);

              data.sources.forEach((source) => {
                  const link = document.createElement('a');
                  link.href = source.url;
                  link.target = '_blank';
                  link.rel = 'noreferrer';
                  link.textContent = source.title || source.url;
                  sourceList.appendChild(link);
              });

              pendingMessage.appendChild(sourceList);
          }

          helpbotHistory.push({ role: 'user', content: message });
          helpbotHistory.push({ role: 'assistant', content: data.reply || '' });
      } catch (error) {
          pendingMessage.textContent = 'The help bot could not reach the inventory right now.';
      }
  });
});
