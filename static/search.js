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
});