document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('item_name');
    const resultContainer = document.getElementById('dropdown-results');

    let activeIndex = -1;

    function highlightItem(index) {
        const items = resultContainer.querySelectorAll('.result-item');
        items.forEach((item, i) => {
            item.classList.toggle('highlighted', i === index);
        });
    }

    searchInput.addEventListener('input', (event) => {
        const query = event.target.value;
        if (query) {
            performSearch(query);
        } else {
            resultContainer.innerHTML = '';
            resultContainer.style.display = 'none';
        }
    });

    searchInput.addEventListener('keydown', (event) => {
        const items = resultContainer.querySelectorAll('.result-item');
        if (!items.length) return;

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            activeIndex = (activeIndex + 1) % items.length;
            highlightItem(activeIndex);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            activeIndex = (activeIndex - 1 + items.length) % items.length;
            highlightItem(activeIndex);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (activeIndex >= 0 && activeIndex < items.length) {
                searchInput.value = items[activeIndex].textContent;
                resultContainer.style.display = 'none';
                activeIndex = -1;
            }
        }
    });

    resultContainer.addEventListener('click', (event) => {
        if (event.target.classList.contains('result-item')) {
            searchInput.value = event.target.textContent;
            resultContainer.style.display = 'none';
        }
    });

    document.addEventListener('click', (event) => {
        if (!event.target.closest('.search-container')) {
            resultContainer.style.display = 'none';
        }
    });
    function displayResults(results, query) {
        resultContainer.innerHTML = '';
        resultContainer.style.display = 'block';
    
        if (results.length > 0) {
            results.forEach((result, index) => {
                const resultDiv = document.createElement('div');
                resultDiv.classList.add('result-item');
                resultDiv.textContent = result.Name;
                resultDiv.addEventListener('mouseenter', () => highlightItem(index));
                resultDiv.addEventListener('mouseleave', () => highlightItem(-1));
                resultContainer.appendChild(resultDiv);
            });
            activeIndex = 0; // Highlight the first item initially
            highlightItem(activeIndex);
        } else {
            const noResultsDiv = document.createElement('div');
            noResultsDiv.classList.add('no-results');
            noResultsDiv.textContent = `Create new item called "${query}"`;
            resultContainer.appendChild(noResultsDiv);
        }
    }
    
    async function performSearch(query) {
        const response = await fetch('/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ search_query: query }),
        });
        const results = await response.json();
        displayResults(results, query);
    }
});

async function submitForm() {
    console.log("submitting form");
    // Prevent default form submission
    const form = document.getElementById('edit-form');
    
    // Collect form data
    const formData = new FormData(form);
    const jsonData = {};
    formData.forEach((value, key) => {
        jsonData[key] = value;
    });
    console.log(jsonData);

    try {
        // Send data to server using Fetch API
        const response = await fetch('/create', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json' // Ensure correct header
            },
            body: JSON.stringify(jsonData) // Send JSON string
        });

        if (response.ok) {
            const result = await response.json();
            // Clear specific fields
            document.getElementById('item_name').value = '';
            document.getElementById('quantity').value = '';

            // Optional: Handle server response (e.g., show a success message)
            console.log('Item added successfully!');
            return result;
        } else {
            console.error('Failed to add item:', response.statusText);
            return null;
        }
    } catch (error) {
        console.error('Error submitting form:', error);
        return null;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const roomSelect = document.getElementById('rooms');
    const wallSelect = document.getElementById('walls');
    const storageTypeSelect = document.getElementById('bin-type');
    const binSelect = document.getElementById('bin');

    // Function to check if all required fields have values
    function checkEnableBinDropdown() {
        if (roomSelect.value && wallSelect.value && storageTypeSelect.value) {
            binSelect.disabled = false;
            fetchBinOptions(); // Fetch bins when all fields are selected
        } else {
            binSelect.disabled = true;
            binSelect.innerHTML = `
            <option value="">--Select Bin--</option>
            <option value="CREATE">CREATE NEW BIN</option>
        `;
        }
    }

    // Add event listeners to the dropdowns
    roomSelect.addEventListener('change', checkEnableBinDropdown);
    wallSelect.addEventListener('change', checkEnableBinDropdown);
    storageTypeSelect.addEventListener('change', checkEnableBinDropdown);

    // Function to fetch bins from the server
    async function fetchBinOptions() {
        const room = roomSelect.value;
        const wall = wallSelect.value;
        const storageType = storageTypeSelect.value;

        try {
            const response = await fetch('/get-bins', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ room, wall, storageType }),
            });

            if (response.ok) {
                const bins = await response.json();
                populateBinDropdown(bins);
            } else {
                console.error('Failed to fetch bins:', response.statusText);
            }
        } catch (error) {
            console.error('Error fetching bins:', error);
        }
    }

    // Function to populate the "Bin Number" dropdown
    function populateBinDropdown(bins) {
        binSelect.innerHTML = `
        <option value="">--Select Bin--</option>
        <option value="CREATE">CREATE NEW BIN</option>
    `;
        bins.forEach(bin => {
            const option = document.createElement('option');
            option.value = bin.id; // Assuming `bin.id` is the unique identifier
            option.textContent = bin.id; // Assuming `bin.name` is the display name
            binSelect.appendChild(option);
        });
    }
});
