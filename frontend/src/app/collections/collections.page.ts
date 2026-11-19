import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import type { OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  IonButton,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardTitle,
  IonContent,
  IonHeader,
  IonIcon,
  IonInput,
  IonItem,
  IonLabel,
  IonMenuButton,
  IonSelect,
  IonSelectOption,
  IonTitle,
  IonToolbar,
  ToastController,
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import { addOutline, trashOutline } from 'ionicons/icons';

import { CollectionService } from '../services/collection';
import type { RecipeCollection } from '../services/collection';
import { RecipeService } from '../services/recipe';

@Component({
  selector: 'app-collections',
  templateUrl: './collections.page.html',
  styleUrl: './collections.page.scss',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    IonButton,
    IonCard,
    IonCardContent,
    IonCardHeader,
    IonCardTitle,
    IonContent,
    IonHeader,
    IonIcon,
    IonInput,
    IonItem,
    IonLabel,
    IonMenuButton,
    IonSelect,
    IonSelectOption,
    IonTitle,
    IonToolbar,
  ],
})
// Let users create collections and manage the recipes saved in them.
export class CollectionsPage implements OnInit {
  private collectionService = inject(CollectionService);
  private recipeService = inject(RecipeService);
  private toastController = inject(ToastController);

  collections: RecipeCollection[] = [];
  recipes = this.recipeService.recipes;
  newCollectionName = '';
  selectedRecipeIds: Record<number, number | undefined> = {};

  // Register the icons used by this page.
  constructor() {
    addIcons({ addOutline, trashOutline });
  }

  // Load available recipes and the user's collections when the page opens.
  ngOnInit(): void {
    this.recipeService.getRecipes().subscribe({
      error: error => {
        console.error('Recipe loading error:', error);
        void this.showToast('Unable to load recipes.');
      },
    });
    this.loadCollections();
  }

  // Fetch the user's collections from the API.
  loadCollections(): void {
    this.collectionService.getCollections().subscribe({
      next: response => {
        this.collections = response.collections ?? [];
      },
      error: error => {
        console.error('Collections loading error:', error);
        void this.showToast('Unable to load collections.');
      },
    });
  }

  // Create a collection using the entered name.
  createCollection(): void {
    const name = this.newCollectionName.trim();
    if (!name) {
      return;
    }

    this.collectionService.createCollection(name).subscribe({
      next: () => {
        this.newCollectionName = '';
        this.loadCollections();
      },
      error: error => {
        console.error('Collection creation error:', error);
        void this.showToast(error.error?.detail || 'Unable to create collection.');
      },
    });
  }

  // Check whether a recipe is already in a collection.
  hasRecipe(collection: RecipeCollection, recipeId: number): boolean {
    return collection.recipes.some(recipe => recipe.id === recipeId);
  }

  // Add the selected recipe to a collection.
  addRecipe(collection: RecipeCollection): void {
    const recipeId = this.selectedRecipeIds[collection.id];
    if (!recipeId) {
      return;
    }

    this.collectionService.addRecipe(collection.id, recipeId).subscribe({
      next: () => {
        this.selectedRecipeIds[collection.id] = undefined;
        this.loadCollections();
      },
      error: error => {
        console.error('Adding recipe to collection failed:', error);
        void this.showToast(error.error?.detail || 'Unable to add recipe to collection.');
      },
    });
  }

  // Remove a recipe from a collection.
  removeRecipe(collection: RecipeCollection, recipeId: number): void {
    this.collectionService.removeRecipe(collection.id, recipeId).subscribe({
      next: () => this.loadCollections(),
      error: error => {
        console.error('Removing recipe from collection failed:', error);
        void this.showToast('Unable to remove recipe from collection.');
      },
    });
  }

  // Confirm and delete a collection.
  deleteCollection(collection: RecipeCollection): void {
    if (!confirm(`Delete the "${collection.name}" collection?`)) {
      return;
    }

    this.collectionService.deleteCollection(collection.id).subscribe({
      next: () => this.loadCollections(),
      error: error => {
        console.error('Collection deletion error:', error);
        void this.showToast('Unable to delete collection.');
      },
    });
  }

  private async showToast(message: string): Promise<void> {
    const toast = await this.toastController.create({
      message,
      duration: 3000,
      position: 'top',
      color: 'danger',
    });
    await toast.present();
  }
}
