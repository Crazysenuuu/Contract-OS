import { test, expect } from '@playwright/test';

test.describe('Agreement Lifecycle', () => {
  test('happy path: create -> sign -> amend -> renew -> terminate', async ({ page }) => {
    // Note: this test assumes a user is already authenticated
    
    // 1. Create
    await page.goto('/agreements/new');
    await page.fill('textarea[placeholder="Describe what you want to do..."]', 'I need a Software License Agreement for our enterprise customers.');
    await page.click('button:has-text("Start wizard")');
    
    // Fill out wizard form
    // Assuming DynamicFormBuilder has some visible fields
    await page.waitForSelector('text=Questionnaire');
    await page.click('button:has-text("Generate Agreement")');
    
    // Wait for redirect to agreement details
    await page.waitForURL(/\/agreements\/[a-zA-Z0-9-]{36}/);
    
    // 2. Sign (using Lifecycle actions)
    // Send for signatures -> All signed
    await page.click('button:has-text("Send for signatures")');
    // For e2e mocking, assume backend handles the transition or we click a "Simulate Signatures" button
    
    // 3. Amend
    await page.click('button:has-text("New amendment")');
    await page.fill('input[placeholder="Amendment title"]', 'Update Payment Terms');
    await page.fill('input[placeholder="Section key, e.g. payment_terms"]', 'payment_terms');
    await page.fill('textarea[placeholder="New wording"]', 'Net 60 days instead of Net 30');
    await page.click('button:has-text("Propose amendment")');
    
    // Activate amendment
    await page.click('button:has-text("Activate")');
    
    // 4. Renew
    await page.click('button:has-text("Edit Configuration")');
    await page.click('label:has-text("Is Renewable") input[type="checkbox"]');
    await page.fill('input[type="number"]', '12'); // Renewal term
    await page.click('button:has-text("Save Configuration")');
    
    await page.click('button:has-text("Force Renew Now")');
    
    // 5. Terminate
    await page.click('button:has-text("Initiate termination")');
    await page.selectOption('select', 'convenience');
    await page.fill('input[placeholder="Notice period (days)"]', '30');
    await page.click('button:has-text("Initiate")');
    
    await page.click('button:has-text("Complete termination")');
    
    // Verify termination status
    await expect(page.locator('span:has-text("terminated")')).toBeVisible();
  });
});
